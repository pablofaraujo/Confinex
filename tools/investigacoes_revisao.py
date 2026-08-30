"""Contrato puro da central de investigações da fila de Revisões.

O módulo planeja somente registros do plano de controle. Ele não conhece rede,
Supabase ou canais e, principalmente, não cria a tripla operacional da fila.
A publicação canônica de Revisões pertence exclusivamente a
``tools/materializar_revisoes_staging.py``. Esta investigação não cria uma
tripla: depois da materialização canônica, ela só pode anexar evidências a um
rascunho já existente e registrar o respectivo evento.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


NAMESPACE = uuid.UUID("a75408b1-9c50-45f8-8fa3-b36af0258927")
VERSAO_POLITICA_PADRAO = "investigacao-v1"
VERSAO_CONSULTA_PADRAO = "consulta-v1"
VERSAO_REGRA_CONFIANCA = "confianca-deterministica-v2"
VERSAO_PROVAS_CAMPOS = "provas-campos-v1"
VERSAO_SCHEMA_POLITICAS = "campos-obrigatorios-v1"
POLITICAS_CAMPOS_OBRIGATORIOS = {
    "compra": ("data", "negocio", "quantidade", "valor_total"),
    "venda": (
        "cabecas", "data_abate", "peso_carcaca_total",
        "prazo_recebimento", "valor_bruto",
    ),
    "pesagem": ("contexto", "data_folha", "peso_kg"),
    "abate": ("cabecas", "data_abate", "lote", "peso_liquido_kg"),
    "documento_fiscal": (
        "data_emissao", "numero_nf", "quantidade",
        "relacao_negocio", "valor_total",
    ),
    "conciliacao_financeira": ("contraparte", "data", "valor"),
    "acerto_confinamento": ("data", "negocio", "valor"),
    # Tipo genérico nunca conclui por pistas factuais: exige decisão humana.
    "revisao": ("decisao_humana",),
}
HASH_SCHEMA_POLITICAS = hashlib.sha256(json.dumps(
    {
        "versao": VERSAO_SCHEMA_POLITICAS,
        "politicas": POLITICAS_CAMPOS_OBRIGATORIOS,
    },
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")).hexdigest()

TABELAS_CONTROLE = frozenset({
    "investigacoes_revisao",
    "investigacao_tarefas",
    "investigacao_evidencias",
    "investigacao_alternativas",
    "investigacao_alternativa_evidencias",
    "investigacao_pendencias",
    "investigacao_eventos",
    "investigacao_entregas",
})
TABELAS_ANEXO = frozenset({"operation_drafts", "eventos"})
TABELAS_OPERACIONAIS = frozenset({
    "operacoes", "compras", "vendas", "abates", "pesagens_caderno",
    "fluxo_caixa", "transacoes_banco", "gtas", "entradas_confinamento",
})

ESTADOS_EXECUCAO = frozenset({
    "pendente", "em_execucao", "aguardando_retentativa", "concluida",
    "cancelada", "obsoleta",
})
ESTADOS_RESULTADO = frozenset({
    "alternativa_unica", "alternativas_multiplas", "divergente",
    "evidencia_insuficiente", "cobertura_incompleta",
})
ESTADOS_COBERTURA = frozenset({
    "completa", "vazio_com_cobertura", "cobertura_incompleta",
    "indisponivel", "reautenticacao_necessaria", "erro_permanente",
})

NIVEIS = {"inconclusivo": 0, "possivel": 1, "provavel": 2, "forte": 3}
SCORES = {"inconclusivo": 0.0, "possivel": 0.35, "provavel": 0.7, "forte": 0.95}
TIPOS_CORRESPONDENCIA = {
    "identificador_exato": "forte",
    "valor_data_contraparte": "provavel",
    "valor_data": "provavel",
    "documento_referenciado": "provavel",
    "nome": "possivel",
    "valor": "possivel",
    "extracao_llm": "possivel",
    "ocr": "possivel",
}
REGRAS_CONFIANCA = {
    "versao": VERSAO_REGRA_CONFIANCA,
    "niveis": NIVEIS,
    "scores": SCORES,
    "tipos_correspondencia": TIPOS_CORRESPONDENCIA,
    "identificador_exato_forte_exige": [
        "cobertura_completa", "uma_correspondencia",
        "mesma_chave_em_linhagem_e_adaptador_independentes_com_cobertura",
    ],
    "caps": {
        "universo_nao_comprovado": "provavel",
        "unicidade_nao_comprovada": "provavel",
        "coerencia_nao_comprovada": "provavel",
        "extracao_nao_confirmada": "possivel",
        "llm_somente_pista": "possivel",
        "ambiguidade_no_campo": "provavel",
        "grupo_correlacao_nao_verificado": "possivel",
    },
    "penalidades": {
        "incoerencia_verificada": "inconclusivo",
        "divergencia_central": "inconclusivo",
    },
    "confianca_geral": "minimo_campos_obrigatorios",
    "linhagem_duplicada_nao_eleva": True,
    "familias_independencia": {
        "agronotas": "fiscal_estruturada",
        "ofx": "financeira_estruturada",
        "ima": "sanitaria_estruturada",
        "telegram": "conversa",
        "wey": "conversa",
        "outro": "auxiliar",
    },
}
HASH_REGRAS_CONFIANCA = hashlib.sha256(json.dumps(
    REGRAS_CONFIANCA, sort_keys=True,
).encode("utf-8")).hexdigest()
SELOS_CONFIANCA_RESERVADOS = frozenset({
    "classificacao", "confianca", "universo_coberto",
    "quantidade_correspondencias", "coerencia_verificada", "avaliador",
    "regra_id", "regra_version", "ruleset_hash", "inputs_hash",
    "inputs_contexto", "inputs_canonico",
    "unico", "coerente",
})

CHAVES_SECRETAS = {
    "token", "access_token", "refresh_token", "authorization", "senha",
    "password", "secret", "service_role", "service_role_key", "apikey",
    "api_key", "headers", "cookie", "cookies",
}
CHAVES_BRUTAS = {
    "json_bruto", "conteudo_bruto", "mensagem_bruta", "payload_bruto",
    "xml_bruto", "ofx_bruto", "documento_bruto", "conversa_integral",
}
CHAVES_TECNICAS_FRONTEND = {
    "origem_conversa_id", "origem_mensagem_id", "grupo_telegram_id",
    "jid", "telefone", "email",
}
FRAGMENTOS_CHAVE_SECRETA = frozenset({
    "authorization", "token", "password", "senha", "secret", "cookie",
    "service_role", "api_key", "apikey", "headers",
})
CHAVES_IDENTIFICADORAS_PUBLICAS = frozenset({
    "operacao_id", "fornecedor_id", "vendedor_id", "contato_id",
    "usuario_id", "entidade_id", "source_draft_id", "pending_action_id",
    "origem_conversa_id", "origem_mensagem_id", "grupo_telegram_id",
    "fonte_registro_id", "jid", "telefone", "email",
    "chave_nfe", "gta_qualificada", "fitid_qualificado", "hash_anexo",
    "identidade_registro", "chave_natural",
})
FONTES_EVIDENCIA = frozenset({
    "nf", "gta", "ofx", "ima", "telegram", "wey", "b3", "planilha", "outro",
})
ADAPTADORES = frozenset({"agronotas", "ofx", "ima", "telegram", "wey", "outro", "sintese"})
ADAPTADORES_FONTES = ADAPTADORES - {"sintese"}
ADAPTADORES_ESTRUTURADOS = frozenset({"agronotas", "ofx", "ima"})
CAMPOS_IDENTIFICADORES_EXATOS = frozenset({
    "chave_nfe", "gta_qualificada", "fitid_qualificado", "hash_anexo",
})

REGISTRO_ADAPTADORES = {
    "agronotas": {
        "versoes": frozenset({"v1"}),
        "identidades": frozenset({"chave_nfe", "gta_qualificada", "hash_anexo"}),
        "familia_fonte": "fiscal_estruturada",
        "autoridade_fonte": "agronotas_fiscal",
        "tabelas": frozenset({
            "evidencias_negocio", "fontes_importacao",
            "negocios_candidatos", "notas_fiscais_xml_raw",
        }),
        "tabelas_nativas": frozenset({"notas_fiscais_xml_raw"}),
    },
    "ofx": {
        "versoes": frozenset({"v1"}),
        "identidades": frozenset({"fitid_qualificado", "hash_anexo"}),
        "familia_fonte": "financeira_estruturada",
        "autoridade_fonte": "instituicao_ofx",
        "tabelas": frozenset({
            "evidencias_negocio", "fontes_importacao",
            "negocios_candidatos", "transacoes_banco_staging",
        }),
        "tabelas_nativas": frozenset({"transacoes_banco_staging"}),
    },
    "ima": {
        "versoes": frozenset({"v1"}),
        "identidades": frozenset({"gta_qualificada", "hash_anexo"}),
        "familia_fonte": "sanitaria_estruturada",
        "autoridade_fonte": "ima_oficial",
        "tabelas": frozenset({
            "evidencias_negocio", "fontes_importacao", "negocios_candidatos",
        }),
        "tabelas_nativas": frozenset({"fontes_importacao"}),
    },
    "telegram": {
        "versoes": frozenset({"v1"}),
        "identidades": frozenset({"hash_anexo"}),
        "familia_fonte": "conversa",
        "autoridade_fonte": "telegram",
        "tabelas": frozenset({
            "evidencias_negocio", "fontes_importacao", "negocios_candidatos",
        }),
        "tabelas_nativas": frozenset({"fontes_importacao"}),
    },
    "wey": {
        "versoes": frozenset({"v1"}),
        "identidades": frozenset({"hash_anexo"}),
        "familia_fonte": "conversa",
        "autoridade_fonte": "whatsapp_wey",
        "tabelas": frozenset({
            "evidencias_negocio", "fontes_importacao", "negocios_candidatos",
        }),
        "tabelas_nativas": frozenset({"fontes_importacao"}),
    },
    "outro": {
        "versoes": frozenset({"v1"}),
        "identidades": frozenset({"hash_anexo"}),
        "familia_fonte": "auxiliar",
        "autoridade_fonte": "auxiliar",
        "tabelas": frozenset({
            "evidencias_negocio", "fontes_importacao", "negocios_candidatos",
        }),
        "tabelas_nativas": frozenset(),
    },
}
_CAPACIDADE_FONTE_SELADA = object()


class FonteAdaptadorSelada:
    """Envelope lógico imutável produzido na fronteira do adaptador.

    O planejador rejeita mappings que imitem este envelope. Assim fatos do
    modelo não podem declarar adaptador, cobertura, linhagem ou identidade.
    Isso separa responsabilidades no processo Python, mas não é uma fronteira
    criptográfica; a ativação exige broker isolado e atestado HMAC externo.
    """

    __slots__ = ("_canonico", "_capacidade")

    def __init__(self, dados: Mapping[str, Any], capacidade: object):
        if capacidade is not _CAPACIDADE_FONTE_SELADA:
            raise ValueError("fonte_selada_somente_pelo_adaptador")
        self._canonico = json.dumps(
            _canon(dados), ensure_ascii=False, separators=(",", ":"),
        )
        self._capacidade = capacidade

    def _dados_confiaveis(self) -> dict[str, Any]:
        if self._capacidade is not _CAPACIDADE_FONTE_SELADA:
            raise ValueError("fonte_selada_invalida")
        return json.loads(self._canonico)


def _texto(valor: Any) -> str:
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texto).strip().casefold()


def _uuid_canonico(valor: Any, codigo_erro: str) -> str:
    try:
        return str(uuid.UUID(str(valor)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(codigo_erro) from exc


def _canon(valor: Any) -> Any:
    if isinstance(valor, Mapping):
        return {str(chave): _canon(valor[chave]) for chave in sorted(valor, key=str)}
    if isinstance(valor, (list, tuple)):
        return [_canon(item) for item in valor]
    if isinstance(valor, (set, frozenset)):
        itens = [_canon(item) for item in valor]
        return sorted(itens, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return valor


def _hash(valor: Any) -> str:
    payload = json.dumps(_canon(valor), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ref_proveniencia(prefixo: str, *partes: Any) -> str:
    payload = ":".join(str(parte) for parte in partes)
    return f"{prefixo}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def _json_canonico(valor: Any) -> str:
    return json.dumps(
        _canon(valor), ensure_ascii=False, separators=(",", ":"),
    )


def _nfe_chave_valida(valor: Any) -> str:
    chave = re.sub(r"\D", "", str(valor or ""))
    if len(chave) != 44:
        raise ValueError("chave_nfe_invalida")
    soma = 0
    peso = 2
    for digito in reversed(chave[:43]):
        soma += int(digito) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    esperado = 0 if resto in (0, 1) else 11 - resto
    if int(chave[-1]) != esperado:
        raise ValueError("chave_nfe_dv_invalido")
    return chave


def _identidade_exata_canonica(
    adaptador: str, identidade: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    if identidade in (None, {}):
        return None
    if not isinstance(identidade, Mapping):
        raise ValueError("identidade_exata_estrutura_invalida")
    tipo = _normalizar_chave(identidade.get("tipo"))
    permitido = REGISTRO_ADAPTADORES[adaptador]["identidades"]
    if tipo not in permitido:
        raise ValueError("identidade_exata_nao_permitida_ao_adaptador")
    valor: Any
    namespace: Any
    if tipo == "chave_nfe":
        valor = _nfe_chave_valida(identidade.get("valor"))
        namespace = {"tipo": tipo, "adaptador": adaptador, "modelo": "nfe44-v1"}
    elif tipo == "hash_anexo":
        valor = _texto(identidade.get("valor"))
        if not re.fullmatch(r"[0-9a-f]{64}", valor):
            raise ValueError("hash_anexo_invalido")
        namespace = {"tipo": tipo, "adaptador": adaptador, "algoritmo": "sha256"}
    elif tipo == "gta_qualificada":
        valor_bruto = identidade.get("valor")
        if not isinstance(valor_bruto, Mapping):
            raise ValueError("gta_qualificada_estrutura_invalida")
        uf = str(valor_bruto.get("uf") or "").strip().upper()
        serie = str(valor_bruto.get("serie") or "").strip().upper()
        ano = str(valor_bruto.get("ano") or "").strip()
        emissor = str(valor_bruto.get("emissor") or "").strip()
        numero = str(valor_bruto.get("numero") or "").strip()
        if (
            not re.fullmatch(r"[A-Z]{2}", uf)
            or not re.fullmatch(r"[A-Z0-9.-]{1,16}", serie)
            or not re.fullmatch(r"20[0-9]{2}", ano)
            or not emissor or len(emissor) > 120
            or not re.fullmatch(r"[A-Z0-9./-]{1,40}", numero.upper())
        ):
            raise ValueError("gta_qualificada_invalida")
        namespace = {
            "tipo": tipo, "uf": uf, "serie": serie, "ano": ano,
            "emissor_hash": _hash(emissor),
        }
        valor = numero.upper()
    elif tipo == "fitid_qualificado":
        valor_bruto = identidade.get("valor")
        if not isinstance(valor_bruto, Mapping):
            raise ValueError("fitid_qualificado_estrutura_invalida")
        instituicao = _texto(valor_bruto.get("instituicao"))
        conta_ref = str(valor_bruto.get("conta_ref") or "").strip()
        fitid = str(valor_bruto.get("fitid") or "").strip()
        if (
            not re.fullmatch(r"[a-z0-9_.-]{2,40}", instituicao)
            or not re.fullmatch(r"[0-9a-f]{64}", conta_ref)
            or not fitid or len(fitid) > 160
        ):
            raise ValueError("fitid_qualificado_invalido")
        namespace = {
            "tipo": tipo, "instituicao": instituicao,
            "conta_ref_hash": _hash(conta_ref),
        }
        valor = fitid
    else:  # coberto pelo registro, mantido fechado por defesa em profundidade
        raise ValueError("identidade_exata_tipo_invalido")
    return {
        "tipo": tipo,
        "namespace_hash": _hash(namespace),
        "valor_hash": _hash(valor),
    }


def selar_fonte_adaptador(
    *,
    adaptador: str,
    versao_adaptador: str,
    consulta: Mapping[str, Any] | str,
    cobertura: str,
    candidatos: Iterable[Mapping[str, Any]] = (),
    linhagem_registrada: str,
    prova_cobertura: Mapping[str, Any],
) -> FonteAdaptadorSelada:
    """Converte saída estruturada do adaptador em envelope interno opaco.

    Esta função pertence ao executor do adaptador, antes de qualquer modelo de
    síntese. ``planejar_investigacao`` não aceita a versão mapping equivalente.
    """
    adaptador_n = _texto(adaptador)
    versao_n = _texto(versao_adaptador)
    if adaptador_n not in REGISTRO_ADAPTADORES:
        raise ValueError("adaptador_nao_registrado")
    if versao_n not in REGISTRO_ADAPTADORES[adaptador_n]["versoes"]:
        raise ValueError("versao_adaptador_nao_registrada")
    if cobertura not in ESTADOS_COBERTURA:
        raise ValueError("cobertura_invalida")
    consulta_n = normalizar_consulta(consulta)
    prova = dict(prova_cobertura or {})
    if set(prova) - {"estado", "inicio_confirmado", "fim_confirmado", "consulta_hash"}:
        raise ValueError("prova_cobertura_campos_invalidos")
    consulta_hash = contrato_consulta(consulta_n)["consulta_hash"]
    completa_provada = (
        prova.get("estado") == "concluida"
        and prova.get("inicio_confirmado") is True
        and prova.get("fim_confirmado") is True
        and prova.get("consulta_hash") == consulta_hash
    )
    if cobertura in {"completa", "vazio_com_cobertura"} and not completa_provada:
        raise ValueError("cobertura_completa_sem_prova")
    linhagem = _texto(linhagem_registrada)
    if not linhagem:
        raise ValueError("linhagem_registrada_invalida")
    candidatos_selados: list[dict[str, Any]] = []
    for bruto in candidatos:
        if not isinstance(bruto, Mapping):
            raise ValueError("candidato_adaptador_invalido")
        candidato = dict(bruto)
        if any(
            str(chave).startswith("_") or str(chave).casefold() in SELOS_CONFIANCA_RESERVADOS
            for chave in candidato
        ):
            raise ValueError("metadado_reservado_ao_mediador")
        identidade = _identidade_exata_canonica(
            adaptador_n, candidato.pop("identidade_registro", None),
        )
        registro_ref = candidato.pop("registro_ref", None)
        fonte_tabela = _normalizar_chave(candidato.pop("fonte_tabela", None))
        fonte_registro_id_bruto = candidato.pop("fonte_registro_id", None)
        fonte_registro_xmin_bruto = candidato.pop("fonte_registro_xmin", None)
        if fonte_tabela and fonte_tabela not in REGISTRO_ADAPTADORES[adaptador_n]["tabelas"]:
            raise ValueError("fonte_tabela_nao_permitida_ao_adaptador")
        if fonte_registro_id_bruto not in (None, "") and not fonte_tabela:
            raise ValueError("fonte_registro_sem_tabela")
        fonte_registro_id = (
            _uuid_canonico(fonte_registro_id_bruto, "fonte_registro_id_invalido")
            if fonte_registro_id_bruto not in (None, "") else None
        )
        if (fonte_registro_id is None) is not (fonte_registro_xmin_bruto in (None, "")):
            raise ValueError("fonte_registro_snapshot_incompleto")
        fonte_registro_xmin = (
            str(fonte_registro_xmin_bruto).strip()
            if fonte_registro_xmin_bruto not in (None, "") else None
        )
        if fonte_registro_xmin is not None and not re.fullmatch(r"[0-9]{1,20}", fonte_registro_xmin):
            raise ValueError("fonte_registro_xmin_invalido")
        for chave in (
            "adaptador", "cobertura", "linhagem", "proveniencia",
            "chave_natural", "fonte_registro_id", "chave_nfe",
            "gta_qualificada", "fitid_qualificado", "hash_anexo",
        ):
            candidato.pop(chave, None)
        candidato["campo"] = _normalizar_chave(candidato.get("campo"))
        declarado = _texto(
            candidato.get("tipo_correspondencia")
            or candidato.get("criterio") or "extracao_llm"
        )
        if identidade and declarado in {"identificador_exato", "documento_referenciado"}:
            candidato["tipo_correspondencia"] = declarado
            candidato["_identidade_exata_tipo"] = identidade["tipo"]
            candidato["_identidade_exata_namespace_hash"] = identidade["namespace_hash"]
            candidato["_identidade_exata_valor_hash"] = identidade["valor_hash"]
        elif declarado in {"ocr", "extracao_llm", "nome", "valor", "valor_data"}:
            candidato["tipo_correspondencia"] = declarado
        else:
            candidato["tipo_correspondencia"] = "extracao_llm"
        candidato["linhagem"] = linhagem
        candidato["_adaptador"] = adaptador_n
        candidato["_familia_fonte"] = REGISTRO_ADAPTADORES[adaptador_n][
            "familia_fonte"
        ]
        candidato["_fonte_tabela"] = fonte_tabela or None
        candidato["_fonte_registro_id"] = fonte_registro_id
        candidato["_registro_origem_ref_observado"] = (
            _ref_proveniencia("src", fonte_tabela, fonte_registro_id)
            if fonte_registro_id is not None else None
        )
        candidato["_snapshot_fonte_ref_observado"] = (
            _ref_proveniencia(
                "snp", fonte_tabela, fonte_registro_id, fonte_registro_xmin,
            )
            if fonte_registro_id is not None else None
        )
        candidato["_origem_classe"] = (
            "nativa"
            if fonte_tabela in REGISTRO_ADAPTADORES[adaptador_n]["tabelas_nativas"]
            and fonte_registro_id is not None
            else "derivada"
        )
        candidato["_autoridade_fonte"] = REGISTRO_ADAPTADORES[adaptador_n][
            "autoridade_fonte"
        ]
        candidato["_dataset_chave"] = (
            candidato["_autoridade_fonte"], fonte_tabela or "sem_tabela"
        )
        candidato["_registro_origem_chave"] = (
            fonte_tabela or "sem_tabela", fonte_registro_id or "sem_registro"
        )
        candidato["_ancestral_chave"] = candidato["_registro_origem_chave"]
        candidato["_correlator_grupo_verificado"] = True
        if registro_ref not in (None, ""):
            registro_ref_n = _texto(registro_ref)
            if not re.fullmatch(r"[0-9a-f]{64}", registro_ref_n):
                raise ValueError("registro_ref_opaco_invalido")
            # A referência opaca é a âncora do registro/documento inteiro. Ela
            # vem antes da identidade específica de cada campo para que peso,
            # data e valor do mesmo documento formem uma alternativa atômica.
            identidade_grupo = {"registro_ref_hash": _hash(registro_ref_n)}
        elif identidade is not None:
            identidade_grupo = identidade
        else:
            identidade_grupo = {"registro_ref_hash": _hash(candidato)}
        candidato["_correlator_grupo_atomico"] = chave_estavel(
            "grupo-atomico", adaptador_n, linhagem, identidade_grupo,
        )
        candidato["_correlator_titulo_atomico"] = sanitizar_payload(
            candidato.get("versao") or candidato.get("alternativa")
            or "Documento conferido",
            proteger_identificadores=True,
        )
        candidatos_selados.append(candidato)
    if cobertura == "vazio_com_cobertura" and candidatos_selados:
        raise ValueError("fonte_vazia_com_resultados_invalida")
    if cobertura == "completa" and not candidatos_selados:
        cobertura = "vazio_com_cobertura"
    chave_fonte = chave_estavel(
        "fonte", adaptador_n, consulta_n, linhagem, versao_n,
    )
    return FonteAdaptadorSelada({
        "_fonte_selada": True,
        "adaptador": adaptador_n,
        "versao_adaptador": versao_n,
        "consulta": consulta_n,
        "cobertura": cobertura,
        "linhagem": linhagem,
        "chave_fonte": chave_fonte,
        "prova_cobertura": prova,
        "candidatos": candidatos_selados,
    }, _CAPACIDADE_FONTE_SELADA)


def _normalizar_chave(chave: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _texto(chave)).strip("_")


def _chave_contem_segredo(chave: Any) -> bool:
    normalizada = _normalizar_chave(chave)
    return (
        normalizada in CHAVES_SECRETAS
        or normalizada in CHAVES_BRUTAS
        or any(fragmento in normalizada for fragmento in FRAGMENTOS_CHAVE_SECRETA)
    )


def _chave_identificadora_publica(chave: Any) -> bool:
    normalizada = _normalizar_chave(chave)
    return (
        normalizada in CHAVES_IDENTIFICADORAS_PUBLICAS
        or normalizada.startswith("origem_")
        or normalizada.endswith("_id")
    )


def _linhagem_opaca(valor: Any) -> str:
    """Preserva independência entre fontes sem persistir o rótulo original."""
    normalizada = _texto(valor or "desconhecida")
    return chave_estavel("lin", normalizada)


def _selar_contexto_confianca(
    avaliacao: dict[str, Any], contexto_base: Mapping[str, Any],
) -> dict[str, Any]:
    """Vincula o resultado publicado aos insumos determinísticos auditáveis."""
    contexto = {
        "base": _canon(contexto_base),
        "classificacao": avaliacao["classificacao"],
        "confianca": avaliacao["confianca"],
        "caps": sorted(set(avaliacao.get("caps") or [])),
        "penalidades": sorted(set(avaliacao.get("penalidades") or [])),
    }
    canonico = _json_canonico(contexto)
    avaliacao["inputs_contexto"] = contexto
    avaliacao["inputs_canonico"] = canonico
    avaliacao["inputs_hash"] = hashlib.sha256(canonico.encode("utf-8")).hexdigest()
    return avaliacao


def chave_estavel(prefixo: str, *partes: Any) -> str:
    return f"{prefixo}_{_hash(partes)[:32]}"


def id_deterministico(tipo: str, chave: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{tipo}:{chave}"))


def normalizar_assunto(assunto: Mapping[str, Any] | str) -> dict[str, Any]:
    bruto = {"titulo": assunto} if isinstance(assunto, str) else dict(assunto)
    return {
        "tipo": _texto(bruto.get("tipo") or "revisao"),
        "titulo": _texto(bruto.get("titulo") or bruto.get("assunto")),
        "referencia": _texto(bruto.get("referencia") or bruto.get("codigo")),
        "contexto_nome": str(bruto.get("contexto_nome") or bruto.get("contexto") or "").strip(),
    }


def campos_politica_assunto(
    assunto_tipo: Any,
    policy_version: str = VERSAO_POLITICA_PADRAO,
) -> list[str]:
    """Deriva campos obrigatórios do registro de política, nunca do adaptador."""
    tipo = _texto(assunto_tipo or "revisao")
    if _texto(policy_version) != VERSAO_POLITICA_PADRAO:
        raise ValueError("versao_politica_nao_registrada")
    try:
        return list(POLITICAS_CAMPOS_OBRIGATORIOS[tipo])
    except KeyError as exc:
        raise ValueError("assunto_tipo_sem_politica") from exc


def normalizar_origem(origem: Mapping[str, Any]) -> dict[str, Any]:
    """Preserva identidade de auditoria, nunca credenciais ou conteúdo integral."""
    resultado = {}
    for campo in (
        "conversa_id", "mensagem_id", "arquivo_hash", "chave_acesso",
        "fitid", "data", "referencia_opaca",
    ):
        if origem.get(campo) not in (None, ""):
            # IDs de canais e bancos são opacos e podem ser sensíveis a caixa
            # ou Unicode. Só removemos espaços externos.
            resultado[campo] = str(origem[campo]).strip()
    resultado["canal"] = _texto(origem.get("canal") or "desconhecido")
    resultado["linhagem"] = _texto(origem.get("linhagem") or resultado["canal"])
    return resultado


def normalizar_consulta(consulta: Mapping[str, Any] | str) -> dict[str, Any]:
    bruto = {"pergunta": consulta} if isinstance(consulta, str) else dict(consulta)
    termos = bruto.get("termos") or bruto.get("tokens") or []
    campos = bruto.get("campos") or []
    if isinstance(termos, str):
        termos = re.split(r"[,;\s]+", termos)
    if isinstance(campos, str):
        campos = re.split(r"[,;]+", campos)
    try:
        limite_bruto = bruto.get("limite")
        limite = 100 if limite_bruto is None else int(limite_bruto)
    except (TypeError, ValueError) as exc:
        raise ValueError("consulta_limite_invalido") from exc
    if not 1 <= limite <= 1000:
        raise ValueError("consulta_limite_invalido")
    return {
        "tipo": _texto(bruto.get("tipo") or "busca_operacional"),
        "pergunta": _texto(bruto.get("pergunta") or bruto.get("consulta")),
        "termos": sorted({_texto(item) for item in termos if _texto(item)}),
        "campos": sorted({_texto(item) for item in campos if _texto(item)}),
        "janela_inicio": _texto(bruto.get("janela_inicio")),
        "janela_fim": _texto(bruto.get("janela_fim")),
        "limite": limite,
        "paginacao": _texto(bruto.get("paginacao") or "inicio"),
        "cobertura_esperada": _texto(
            bruto.get("cobertura_esperada") or "contexto_completo"
        ),
    }


def contrato_consulta(consulta: Mapping[str, Any] | str) -> dict[str, Any]:
    """Sela a consulta completa para persistência e retomada determinística."""
    spec = normalizar_consulta(consulta)
    canonico = _json_canonico(spec)
    consulta_hash = hashlib.sha256(canonico.encode("utf-8")).hexdigest()
    return {
        "consulta_ref": f"qref_{consulta_hash[:32]}",
        "consulta_schema_version": VERSAO_CONSULTA_PADRAO,
        "consulta_spec": spec,
        "consulta_canonico": canonico,
        "consulta_hash": consulta_hash,
    }


def resolver_consulta_tarefa(tarefa: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstrói a consulta só da linha durável e verifica sua integridade."""
    contrato = contrato_consulta(tarefa.get("consulta_spec") or {})
    if tarefa.get("consulta_schema_version") != contrato["consulta_schema_version"]:
        raise ValueError("consulta_schema_version_incompativel")
    if tarefa.get("consulta_canonico") != contrato["consulta_canonico"]:
        raise ValueError("consulta_canonico_divergente")
    if tarefa.get("consulta_hash") != contrato["consulta_hash"]:
        raise ValueError("consulta_hash_divergente")
    if tarefa.get("consulta_ref") != contrato["consulta_ref"]:
        raise ValueError("consulta_ref_divergente")
    return contrato["consulta_spec"]


def chaves_investigacao(
    assunto: Mapping[str, Any] | str,
    origem: Mapping[str, Any],
    consulta: Mapping[str, Any] | str,
    *,
    fingerprint_base: str,
    versao_politica: str = VERSAO_POLITICA_PADRAO,
    adaptador: str = "outro",
    versao_adaptador: str = "v1",
    chave_natural_evidencia: str | None = None,
    plano_hash: str | None = None,
    vinculo_assunto: Mapping[str, Any] | str | None = None,
) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint_base or ""):
        raise ValueError("fingerprint_base_invalido")
    versao_politica_n = _texto(versao_politica)
    versao_adaptador_n = _texto(versao_adaptador)
    if not versao_politica_n:
        raise ValueError("versao_politica_invalida")
    if not versao_adaptador_n:
        raise ValueError("versao_adaptador_invalida")
    assunto_n = normalizar_assunto(assunto)
    assunto_identidade = dict(assunto_n)
    assunto_identidade["contexto_nome"] = _texto(assunto_n["contexto_nome"])
    origem_n = normalizar_origem(origem)
    consulta_n = normalizar_consulta(consulta)
    plano_hash_n = plano_hash or _hash({
        "fontes": [{
            "adaptador": _texto(adaptador),
            "adaptador_version": versao_adaptador_n,
            "consulta_hash": _hash(consulta_n),
            "linhagem": origem_n.get("linhagem"),
        }],
        "campos_obrigatorios": [],
    })
    if not re.fullmatch(r"[0-9a-f]{64}", plano_hash_n):
        raise ValueError("plano_hash_invalido")
    investigacao = chave_estavel(
        "inv", assunto_identidade, origem_n, fingerprint_base,
        versao_politica_n, plano_hash_n,
        _canon(vinculo_assunto or {"tipo": "sem_vinculo"}),
    )
    tarefa = chave_estavel(
        "tar", investigacao, _texto(adaptador), consulta_n, versao_adaptador_n
    )
    evidencia = chave_estavel(
        "evi", origem_n.get("linhagem"), chave_natural_evidencia or origem_n, consulta_n
    )
    materializacao = chave_estavel("mat", investigacao, fingerprint_base)
    return {
        "investigacao": investigacao,
        "tarefa": tarefa,
        "evidencia": evidencia,
        "materializacao": materializacao,
    }


def classificar_cobertura(
    estado: str,
    *,
    resultados: Iterable[Any] = (),
    cobertura_completa: bool = True,
) -> str:
    estado_n = _texto(estado).replace(" ", "_")
    if estado_n in {
        "indisponivel", "reautenticacao_necessaria", "erro_permanente",
        "cobertura_incompleta",
    }:
        return estado_n
    encontrados = bool(list(resultados))
    if not cobertura_completa:
        return "cobertura_incompleta"
    return "completa" if encontrados else "vazio_com_cobertura"


def _identidade_exata_interna(candidato: Mapping[str, Any]) -> tuple[str, str, str] | None:
    tipo = _normalizar_chave(candidato.get("_identidade_exata_tipo"))
    namespace_hash = str(candidato.get("_identidade_exata_namespace_hash") or "")
    valor_hash = str(candidato.get("_identidade_exata_valor_hash") or "")
    if (
        tipo not in CAMPOS_IDENTIFICADORES_EXATOS
        or not re.fullmatch(r"[0-9a-f]{64}", namespace_hash)
        or not re.fullmatch(r"[0-9a-f]{64}", valor_hash)
    ):
        return None
    return tipo, namespace_hash, valor_hash


def _prova_campo(candidato: Mapping[str, Any]) -> dict[str, Any]:
    identidade = _identidade_exata_interna(candidato)
    return {
        "criterio": _texto(
            candidato.get("tipo_correspondencia")
            or candidato.get("criterio") or "desconhecido"
        ),
        "identidade_tipo": identidade[0] if identidade else None,
        "identidade_namespace_hash": identidade[1] if identidade else None,
        "identidade_valor_hash": identidade[2] if identidade else None,
    }


def _correlacionar_candidatos(
    candidatos: Iterable[Mapping[str, Any]],
    *,
    cobertura_por_fonte: Mapping[str, str] | None = None,
    cobertura_padrao: str = "cobertura_incompleta",
) -> list[dict[str, Any]]:
    """Calcula selos internos; nunca aceita confiança pronta do adaptador."""
    itens = [dict(item) for item in candidatos]
    coberturas = dict(cobertura_por_fonte or {})
    contagens: dict[
        tuple[str, str, tuple[str, str, str]], set[str]
    ] = defaultdict(set)
    valores_identidade: dict[
        str, dict[tuple[str, str, str], dict[str, set[str]]]
    ] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(set))
    )
    for item in itens:
        fonte = str(
            item.get("_chave_fonte")
            or _texto(item.get("linhagem") or item.get("fonte"))
            or "fonte-padrao"
        )
        campo = str(item.get("campo") or "").strip()
        tipo = _texto(item.get("tipo_correspondencia") or item.get("criterio"))
        identidade = _identidade_exata_interna(item)
        if campo and tipo == "identificador_exato" and identidade:
            grupo_atomico = str(
                item.get("_correlator_grupo_atomico")
                or chave_estavel("grupo-correlacao", fonte, campo, identidade)
            )
            contagens[(fonte, campo, identidade)].add(grupo_atomico)
        adaptador = _texto(item.get("_adaptador"))
        if (
            campo
            and adaptador in ADAPTADORES_ESTRUTURADOS
            and coberturas.get(fonte, cobertura_padrao) == "completa"
            and tipo in {"identificador_exato", "documento_referenciado"}
            and identidade is not None
        ):
            valores_identidade[campo][identidade][_hash(item.get("valor"))].add(fonte)
    saida: list[dict[str, Any]] = []
    for item in itens:
        fonte = str(
            item.get("_chave_fonte")
            or _texto(item.get("linhagem") or item.get("fonte"))
            or "fonte-padrao"
        )
        campo = str(item.get("campo") or "").strip()
        tipo = _texto(item.get("tipo_correspondencia") or item.get("criterio"))
        cobertura = coberturas.get(fonte, cobertura_padrao)
        adaptador = _texto(item.get("_adaptador"))
        identidade = _identidade_exata_interna(item)
        item["_correlator_universo_coberto"] = cobertura == "completa"
        item["_correlator_quantidade_correspondencias"] = len(contagens.get(
            (fonte, campo, identidade), set()
        )) if identidade else 0
        valores_por_identidade = (
            valores_identidade.get(campo, {}).get(identidade, {})
            if identidade else {}
        )
        fontes_divergentes = {
            fonte_item
            for fontes in valores_por_identidade.values()
            for fonte_item in fontes
        }
        divergencia_deterministica = (
            len(valores_por_identidade) > 1 and len(fontes_divergentes) > 1
        )
        if divergencia_deterministica:
            coerencia: bool | None = False
        else:
            linhagem = _texto(item.get("linhagem") or item.get("fonte"))
            valor_hash = _hash(item.get("valor"))
            coerencia = True if adaptador in ADAPTADORES_ESTRUTURADOS and any(
                outro is not item
                and str(outro.get("campo") or "").strip() == campo
                and _hash(outro.get("valor")) == valor_hash
                and _identidade_exata_interna(outro) == identidade
                and identidade is not None
                and _texto(outro.get("linhagem") or outro.get("fonte")) != linhagem
                and _texto(outro.get("_adaptador")) != adaptador
                and _texto(outro.get("_familia_fonte"))
                    != _texto(item.get("_familia_fonte"))
                and item.get("_origem_classe") == "nativa"
                and outro.get("_origem_classe") == "nativa"
                and item.get("_autoridade_fonte")
                    != outro.get("_autoridade_fonte")
                and item.get("_dataset_chave")
                    != outro.get("_dataset_chave")
                and item.get("_registro_origem_chave")
                    != outro.get("_registro_origem_chave")
                and item.get("_ancestral_chave")
                    != outro.get("_ancestral_chave")
                and _texto(outro.get("_adaptador")) in ADAPTADORES_ESTRUTURADOS
                and str(
                    outro.get("_chave_fonte")
                    or _texto(outro.get("linhagem") or outro.get("fonte"))
                    or "fonte-padrao"
                ) != fonte
                and _texto(
                    outro.get("tipo_correspondencia") or outro.get("criterio")
                ) in {"identificador_exato", "documento_referenciado"}
                and coberturas.get(str(
                    outro.get("_chave_fonte")
                    or _texto(outro.get("linhagem") or outro.get("fonte"))
                    or "fonte-padrao"
                ), cobertura_padrao) == "completa"
                and not divergencia_deterministica
                for outro in itens
            ) else None
        item["_correlator_coerencia_verificada"] = coerencia
        item["_correlator_divergencia_central"] = divergencia_deterministica
        item["_correlator_extracao_confirmada"] = (
            adaptador in ADAPTADORES_ESTRUTURADOS
            and tipo in {"identificador_exato", "documento_referenciado"}
            and identidade is not None
        )
        item["_correlator_aritmetica_consistente"] = None
        item["_correlator_avaliador"] = "correlator"
        item["_correlator_ruleset_hash"] = HASH_REGRAS_CONFIANCA
        saida.append(item)
    return saida


def _avaliar_evidencia(candidato: Mapping[str, Any]) -> dict[str, Any]:
    tipo = _texto(candidato.get("tipo_correspondencia") or candidato.get("criterio"))
    campo = _normalizar_chave(candidato.get("campo"))
    identidade = _identidade_exata_interna(candidato)
    if tipo == "identificador_exato" and identidade is None:
        tipo = "documento_referenciado"
    nivel = TIPOS_CORRESPONDENCIA.get(tipo, "inconclusivo")
    penalidades: list[str] = []
    caps: list[str] = []
    if tipo == "identificador_exato":
        universo_coberto = candidato.get("_correlator_universo_coberto") is True
        quantidade = candidato.get("_correlator_quantidade_correspondencias")
        coerencia = candidato.get("_correlator_coerencia_verificada")
        if not universo_coberto:
            caps.append("universo_nao_comprovado")
        if quantidade != 1:
            caps.append("unicidade_nao_comprovada")
        if coerencia is not True:
            caps.append("coerencia_nao_comprovada")
        if coerencia is False:
            nivel = "inconclusivo"
            penalidades.append("incoerencia_verificada")
        elif caps:
            nivel = "provavel"
    if (
        candidato.get("_correlator_extracao_confirmada") is False
        and NIVEIS[nivel] > NIVEIS["possivel"]
    ):
        nivel = "possivel"
        caps.append("extracao_nao_confirmada")
    if tipo == "extracao_llm":
        if NIVEIS[nivel] > NIVEIS["possivel"]:
            nivel = "possivel"
        caps.append("llm_somente_pista")
    if candidato.get("_correlator_divergencia_central") is True:
        nivel = "inconclusivo"
        penalidades.append("divergencia_central")
    entrada_regra = {
        "tipo_correspondencia": tipo or "desconhecido",
        "valor_hash": _hash(candidato.get("valor")),
        "identidade_tipo": identidade[0] if identidade else None,
        "identidade_namespace_hash": identidade[1] if identidade else None,
        "identidade_valor_hash": identidade[2] if identidade else None,
        "linhagem": _linhagem_opaca(
            candidato.get("linhagem") or candidato.get("fonte")
        ),
        "universo_coberto": candidato.get("_correlator_universo_coberto") is True,
        "quantidade_correspondencias": candidato.get(
            "_correlator_quantidade_correspondencias"
        ),
        "coerencia_verificada": candidato.get("_correlator_coerencia_verificada"),
        "extracao_confirmada": candidato.get("_correlator_extracao_confirmada"),
        "aritmetica_consistente": candidato.get(
            "_correlator_aritmetica_consistente"
        ),
        "divergencia_central": bool(
            candidato.get("_correlator_divergencia_central")
        ),
    }
    return {
        "nivel": nivel,
        "regra_id": "correspondencia_deterministica",
        "regra_version": VERSAO_REGRA_CONFIANCA,
        "avaliador": candidato.get("_correlator_avaliador"),
        "ruleset_hash": candidato.get("_correlator_ruleset_hash"),
        "inputs_contexto": _canon(entrada_regra),
        "penalidades": sorted(set(penalidades)),
        "caps": sorted(set(caps)),
    }


def _nivel_evidencia(candidato: Mapping[str, Any]) -> str:
    return _avaliar_evidencia(candidato)["nivel"]


def confianca_explicavel(
    candidatos: Iterable[Mapping[str, Any]],
    *,
    policy_version: str = VERSAO_POLITICA_PADRAO,
    cobertura_por_fonte: Mapping[str, str] | None = None,
    cobertura_padrao: str = "cobertura_incompleta",
) -> dict[str, Any]:
    """Agrupa alternativas sem converter quantidade de fontes em confiança 100%."""
    agrupados: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    divergencias: set[str] = set()
    candidatos_correlacionados = _correlacionar_candidatos(
        candidatos,
        cobertura_por_fonte=cobertura_por_fonte,
        cobertura_padrao=cobertura_padrao,
    )
    for candidato in candidatos_correlacionados:
        campo = str(candidato.get("campo") or "").strip()
        valor = candidato.get("valor")
        if not campo or valor in (None, ""):
            continue
        linhagem = _linhagem_opaca(
            candidato.get("linhagem") or candidato.get("fonte") or "desconhecida"
        )
        chave_valor = _hash(valor)
        alternativa = agrupados[campo].setdefault(chave_valor, {
            "valor": valor,
            "linhagens": set(),
            "evidencias": [],
            "niveis": [],
            "avaliacoes": [],
        })
        avaliacao = _avaliar_evidencia(candidato)
        nivel = avaliacao["nivel"]
        alternativa["linhagens"].add(linhagem)
        alternativa["niveis"].append(nivel)
        alternativa["avaliacoes"].append(avaliacao)
        evidencia = candidato.get("evidencia") or candidato.get("resumo")
        if evidencia and evidencia not in alternativa["evidencias"]:
            alternativa["evidencias"].append(str(evidencia))
        if candidato.get("_correlator_divergencia_central") is True:
            divergencias.add(campo)

    saida: dict[str, Any] = {}
    for campo, valores in agrupados.items():
        alternativas = []
        for item in valores.values():
            tem_incoerencia = any(
                "divergencia_central" in avaliacao["penalidades"]
                or "incoerencia_verificada" in avaliacao["penalidades"]
                for avaliacao in item["avaliacoes"]
            )
            nivel = "inconclusivo" if tem_incoerencia else max(
                item["niveis"], key=lambda valor: NIVEIS[valor]
            )
            # Linhagens independentes reforçam a explicação, mas não elevam a
            # força além da melhor chave verificável observada.
            contexto_base = {
                "avaliacoes": sorted(
                    (avaliacao["inputs_contexto"] for avaliacao in item["avaliacoes"]),
                    key=_hash,
                ),
                "ambiguidade_no_campo": False,
                "grupo_correlacao_verificado": True,
            }
            alternativa = {
                "valor": item["valor"],
                "linhagens": sorted(item["linhagens"]),
                "evidencias": item["evidencias"],
                "classificacao": nivel,
                "confianca": SCORES[nivel],
                "regra_id": "correspondencia_deterministica",
                "regra_version": VERSAO_REGRA_CONFIANCA,
                "policy_version": _texto(policy_version),
                "avaliador": "correlator",
                "ruleset_hash": HASH_REGRAS_CONFIANCA,
                "penalidades": sorted({
                    penalidade
                    for avaliacao in item["avaliacoes"]
                    for penalidade in avaliacao["penalidades"]
                }),
                "caps": sorted({
                    cap
                    for avaliacao in item["avaliacoes"]
                    for cap in avaliacao["caps"]
                }),
            }
            _selar_contexto_confianca(alternativa, contexto_base)
            alternativas.append(alternativa)
        if len(alternativas) > 1:
            for alternativa in alternativas:
                if alternativa["classificacao"] == "forte":
                    alternativa["classificacao"] = "provavel"
                    alternativa["confianca"] = SCORES["provavel"]
                alternativa["caps"] = sorted(set(
                    alternativa["caps"] + ["ambiguidade_no_campo"]
                ))
                contexto_base = dict(alternativa["inputs_contexto"]["base"])
                contexto_base["ambiguidade_no_campo"] = True
                _selar_contexto_confianca(alternativa, contexto_base)
        saida[campo] = {
            "alternativas": alternativas,
            "ambiguo": len(alternativas) > 1,
            "divergente": campo in divergencias,
        }
    return saida


def resultado_investigacao(
    confianca_campos: Mapping[str, Any],
    *,
    cobertura: str,
    campos_obrigatorios: Sequence[str] = (),
    fingerprint_obsoleto: bool = False,
) -> str:
    if fingerprint_obsoleto:
        return "evidencia_insuficiente"
    if cobertura in {
        "cobertura_incompleta", "indisponivel", "reautenticacao_necessaria",
        "erro_permanente",
    }:
        return "cobertura_incompleta"
    if any(
        item.get("divergente") and len(item.get("alternativas") or []) > 1
        for item in confianca_campos.values()
    ):
        return "divergente"
    if any(item.get("divergente") for item in confianca_campos.values()):
        return "evidencia_insuficiente"
    if any(
        not (item.get("alternativas") or [])
        or any(
            float(alternativa.get("confianca") or 0) < SCORES["provavel"]
            for alternativa in item.get("alternativas") or []
        )
        for item in confianca_campos.values()
    ):
        return "evidencia_insuficiente"
    if any(item.get("ambiguo") for item in confianca_campos.values()):
        return "alternativas_multiplas"
    ausentes = [campo for campo in campos_obrigatorios if campo not in confianca_campos]
    if not confianca_campos or ausentes:
        return "evidencia_insuficiente"
    return "alternativa_unica"


def sanitizar_payload(
    payload: Any, *, proteger_identificadores: bool = False,
) -> Any:
    """Remove segredos e PII; evidência pública também oculta identificadores."""
    if isinstance(payload, Mapping):
        saida = {}
        for chave, valor in payload.items():
            if _chave_contem_segredo(chave):
                continue
            if proteger_identificadores and (
                _chave_identificadora_publica(chave) or str(chave).startswith("_")
            ):
                continue
            chave_normalizada = _normalizar_chave(chave)
            if (
                not proteger_identificadores
                and chave_normalizada.endswith("_hash")
                and isinstance(valor, str)
                and re.fullmatch(r"[0-9a-f]{64}", valor)
            ):
                # Um digest só atravessa intacto quando o contrato do próprio
                # campo declara `_hash` e o formato SHA-256 é exato. Segredos
                # continuam bloqueados acima pela allowlist de nomes.
                saida[str(chave)] = valor
                continue
            if (
                not proteger_identificadores
                and (
                    chave_normalizada.endswith("_ref")
                    or chave_normalizada == "chave_idempotencia"
                )
                and isinstance(valor, str)
                and re.fullmatch(r"[a-z][a-z0-9-]{1,30}_[0-9a-f]{32}", valor)
            ):
                saida[str(chave)] = valor
                continue
            if (
                not proteger_identificadores
                and (chave_normalizada == "id" or chave_normalizada.endswith("_id"))
                and isinstance(valor, str)
            ):
                try:
                    saida[str(chave)] = str(uuid.UUID(valor))
                    continue
                except (ValueError, TypeError, AttributeError):
                    pass
            if (
                not proteger_identificadores
                and chave_normalizada.endswith("_ids")
                and isinstance(valor, (list, tuple))
            ):
                try:
                    saida[str(chave)] = [str(uuid.UUID(str(item))) for item in valor]
                    continue
                except (ValueError, TypeError, AttributeError):
                    pass
            if (
                not proteger_identificadores
                and chave_normalizada.endswith("_canonico")
                and isinstance(valor, str)
            ):
                nome_hash = f"{chave_normalizada[:-9]}_hash"
                hash_irmao = next((
                    item
                    for nome, item in payload.items()
                    if _normalizar_chave(nome) == nome_hash
                ), None)
                if (
                    isinstance(hash_irmao, str)
                    and re.fullmatch(r"[0-9a-f]{64}", hash_irmao)
                    and hashlib.sha256(valor.encode("utf-8")).hexdigest()
                        == hash_irmao
                ):
                    # O texto canônico só é preservado quando o digest irmão
                    # prova exatamente seu conteúdo. Não há exceção genérica
                    # para strings que apenas se parecem com identificadores.
                    saida[str(chave)] = valor
                    continue
            if (
                not proteger_identificadores
                and chave_normalizada in {"linhagem", "fingerprint_base"}
                and isinstance(valor, str)
                and (
                    re.fullmatch(r"lin_[0-9a-f]{32}", valor)
                    if chave_normalizada == "linhagem"
                    else re.fullmatch(r"[0-9a-f]{64}", valor)
                )
            ):
                saida[str(chave)] = valor
                continue
            saida[str(chave)] = sanitizar_payload(
                valor, proteger_identificadores=proteger_identificadores,
            )
        return saida
    if isinstance(payload, (list, tuple)):
        return [
            sanitizar_payload(
                item, proteger_identificadores=proteger_identificadores,
            )
            for item in payload
        ]
    if isinstance(payload, str):
        texto = re.sub(r"(?i)\bbearer\s+\S+", "[DADO PROTEGIDO]", payload)
        texto = re.sub(
            r"(?i)\b(?:x[-_ ]?api[-_ ]?key|api[-_ ]?key|authorization|"
            r"access[-_ ]?token|refresh[-_ ]?token|password|senha|secret)"
            r"\s*[:=]\s*\S+",
            "[DADO PROTEGIDO]",
            texto,
        )
        texto = re.sub(
            r"(?i)\b(?:sk|sbp|eyj)[a-z0-9._-]{12,}\b",
            "[DADO PROTEGIDO]",
            texto,
        )
        texto = re.sub(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            "[CONTATO PROTEGIDO]",
            texto,
            flags=re.I,
        )
        texto = re.sub(
            r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)",
            "[DOCUMENTO PROTEGIDO]", texto,
        )
        texto = re.sub(
            r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)",
            "[DOCUMENTO PROTEGIDO]", texto,
        )
        texto = re.sub(
            r"(?<![0-9A-Za-z-])(?:\+?55[ .-]?)?(?:\(?\d{2}\)?[ .-]?)"
            r"(?:9\d{4}|\d{4})[ .-]?\d{4}(?![0-9A-Za-z-])",
            "[CONTATO PROTEGIDO]", texto,
        )
        texto = re.sub(
            r"(?i)\b(?:cpf|cnpj)[^0-9]{0,12}\d{11,14}(?!\d)",
            "[DOCUMENTO PROTEGIDO]", texto,
        )
        texto = re.sub(
            r"(?<!\d)\d{11,14}(?!\d)",
            "[DOCUMENTO PROTEGIDO]", texto,
        )
        if proteger_identificadores:
            texto = re.sub(
                r"(?i)\b(?:nfe|chave[ _-]?(?:nfe|fiscal))"
                r"[^0-9]{0,16}\d{44}(?!\d)",
                "[REFERÊNCIA PROTEGIDA]", texto,
            )
            texto = re.sub(
                r"(?<!\d)\d{44}(?!\d)",
                "[REFERÊNCIA PROTEGIDA]", texto,
            )
            texto = re.sub(
                r"(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}",
                "[REFERÊNCIA PROTEGIDA]", texto,
            )
        return texto
    if isinstance(payload, int) and not isinstance(payload, bool):
        tamanho = len(str(abs(payload)))
        if 11 <= tamanho <= 14:
            return "[DOCUMENTO PROTEGIDO]"
        if proteger_identificadores and tamanho == 44:
            return "[REFERÊNCIA PROTEGIDA]"
    return payload


def _validar_instante(instante: str) -> str:
    valor = str(instante or "").strip()
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("instante_referencia_invalido") from exc
    if data.tzinfo is None or data.utcoffset() is None:
        raise ValueError("instante_referencia_sem_fuso")
    try:
        data_utc = data.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError("instante_referencia_fora_janela_operacional") from exc
    if not (
        datetime(2000, 1, 1, tzinfo=timezone.utc)
        <= data_utc
        < datetime(2200, 1, 1, tzinfo=timezone.utc)
    ):
        raise ValueError("instante_referencia_fora_janela_operacional")
    return data_utc.isoformat().replace("+00:00", "Z")


def _fonte_tipo(candidato: Mapping[str, Any], adaptador: str) -> str:
    # O rótulo público é propriedade do adaptador atestado, nunca do fato ou
    # do modelo. Isso impede uma tarefa OFX de se apresentar como NF/IMA.
    return {
        "agronotas": "nf",
        "ofx": "ofx",
        "ima": "ima",
        "telegram": "telegram",
        "wey": "wey",
    }.get(_texto(adaptador), "outro")


def _alternativas_planejadas(
    candidatos: Sequence[Mapping[str, Any]],
    confianca: Mapping[str, Any],
    *,
    policy_version: str,
    campos_obrigatorios: Sequence[str],
) -> list[dict[str, Any]]:
    """Monta versões conservadoras; nunca combina ambiguidades sem vínculo explícito."""
    unicos = {
        campo: dados["alternativas"][0]["valor"]
        for campo, dados in confianca.items()
        if len(dados.get("alternativas") or []) == 1
    }
    ambiguos = {
        campo: dados["alternativas"]
        for campo, dados in confianca.items()
        if len(dados.get("alternativas") or []) > 1
    }
    grupos_explicitos: dict[str, list[int]] = defaultdict(list)
    for indice, candidato in enumerate(candidatos):
        grupo = (
            candidato.get("_correlator_grupo_atomico")
            or candidato.get("versao") or candidato.get("alternativa")
            or candidato.get("grupo_correlacao")
        )
        if grupo not in (None, ""):
            grupos_explicitos[str(grupo).strip()].append(indice)

    propostas: list[tuple[str, dict[str, Any], list[int]]] = []
    if grupos_explicitos:
        for titulo, indices in sorted(grupos_explicitos.items()):
            titulo_publico = next((
                sanitizar_payload(
                    candidatos[indice].get("_correlator_titulo_atomico"),
                    proteger_identificadores=True,
                )
                for indice in indices
                if candidatos[indice].get("_correlator_titulo_atomico")
            ), "Versão encontrada")
            if not all(
                candidatos[indice].get("_correlator_grupo_verificado") is True
                for indice in indices
            ):
                for ordem, indice in enumerate(indices, 1):
                    campo = str(candidatos[indice].get("campo") or "").strip()
                    valor = candidatos[indice].get("valor")
                    if campo and valor not in (None, ""):
                        propostas.append((
                            f"{titulo_publico} · pista {ordem}", {campo: valor}, [indice]
                        ))
                continue
            # Um rótulo externo não prova que fontes distintas formam uma só
            # versão. Particionamos por linhagem e nunca usamos last-write-wins.
            por_linhagem_grupo: dict[str, list[int]] = defaultdict(list)
            for indice in indices:
                linhagem = _texto(
                    candidatos[indice].get("linhagem")
                    or candidatos[indice].get("fonte") or "desconhecida"
                )
                por_linhagem_grupo[linhagem].append(indice)
            for ordem, (_, indices_linhagem) in enumerate(
                sorted(por_linhagem_grupo.items()), 1
            ):
                por_campo: dict[str, list[tuple[int, Any]]] = defaultdict(list)
                for indice in indices_linhagem:
                    campo = str(candidatos[indice].get("campo") or "").strip()
                    valor = candidatos[indice].get("valor")
                    if campo and valor not in (None, ""):
                        por_campo[campo].append((indice, valor))
                comuns: dict[str, Any] = {}
                indices_comuns: list[int] = []
                conflitos: dict[str, list[tuple[int, Any]]] = {}
                for campo, ocorrencias in por_campo.items():
                    valores_unicos: dict[str, tuple[int, Any]] = {}
                    for indice, valor in ocorrencias:
                        valores_unicos.setdefault(_hash(valor), (indice, valor))
                    if len(valores_unicos) == 1:
                        indice, valor = next(iter(valores_unicos.values()))
                        comuns[campo] = valor
                        indices_comuns.append(indice)
                    else:
                        conflitos[campo] = list(valores_unicos.values())
                rotulo = (
                    titulo_publico if len(por_linhagem_grupo) == 1
                    else f"{titulo_publico} · fonte {ordem}"
                )
                if not conflitos and comuns:
                    propostas.append((rotulo, comuns, indices_comuns))
                    continue
                if comuns:
                    propostas.append((f"{rotulo} · dados comuns", comuns, indices_comuns))
                for campo, ocorrencias in sorted(conflitos.items()):
                    for numero, (indice, valor) in enumerate(ocorrencias, 1):
                        propostas.append((
                            f"{rotulo} · opção {numero} de {campo}",
                            {**comuns, campo: valor},
                            sorted(set(indices_comuns + [indice])),
                        ))
        # A existência de um grupo explícito não autoriza descartar as pistas
        # sem grupo. Elas ficam como opções parciais independentes até que uma
        # chave verificável prove a qual versão pertencem.
        for indice, candidato in enumerate(candidatos):
            grupo = (
                candidato.get("_correlator_grupo_atomico")
                or candidato.get("versao") or candidato.get("alternativa")
                or candidato.get("grupo_correlacao")
            )
            if grupo in (None, "") and candidato.get("valor") not in (None, ""):
                campo = str(candidato.get("campo") or "").strip()
                if campo:
                    rotulo = _texto(candidato.get("_adaptador") or "fonte")
                    propostas.append((
                        f"Pista de {rotulo}", {campo: candidato.get("valor")},
                        [indice],
                    ))
    elif not ambiguos:
        # Sem versão/proveniência explícita, cada linhagem permanece parcial.
        # Isto impede fundir campos de documentos ou conversas incompatíveis.
        for indice, candidato in enumerate(candidatos):
            if candidato.get("campo") and candidato.get("valor") not in (None, ""):
                campo = str(candidato.get("campo") or "").strip()
                rotulo = _texto(
                    candidato.get("_adaptador") or "fonte"
                )
                propostas.append((
                    f"Pista de {rotulo}", {campo: candidato.get("valor")},
                    [indice],
                ))
    elif len(ambiguos) == 1:
        campo, opcoes = next(iter(ambiguos.items()))
        for numero, opcao in enumerate(opcoes, 1):
            snapshot = {}
            snapshot[campo] = opcao["valor"]
            indices = [
                i for i, candidato in enumerate(candidatos)
                if str(candidato.get("campo") or "").strip() == campo
                and _hash(candidato.get("valor")) == _hash(opcao["valor"])
            ]
            propostas.append((f"Opção {numero}", snapshot, indices))
    else:
        # Sem um identificador de versão, não fazemos produto cartesiano entre
        # campos divergentes. Cada pista vira opção parcial para decisão humana.
        vistos: set[str] = set()
        for indice, candidato in enumerate(candidatos):
            campo = str(candidato.get("campo") or "").strip()
            if campo not in ambiguos or candidato.get("valor") in (None, ""):
                continue
            snapshot = {}
            snapshot[campo] = candidato.get("valor")
            chave = _hash(snapshot)
            if chave in vistos:
                continue
            vistos.add(chave)
            propostas.append((f"Opção para {campo}", snapshot, [indice]))

    propostas_unicas: dict[str, tuple[str, dict[str, Any], list[int]]] = {}
    for titulo, snapshot, indices in propostas:
        chave_snapshot = _hash(snapshot)
        if chave_snapshot in propostas_unicas:
            titulo_anterior, snapshot_anterior, indices_anteriores = propostas_unicas[chave_snapshot]
            propostas_unicas[chave_snapshot] = (
                titulo_anterior, snapshot_anterior,
                sorted(set(indices_anteriores) | set(indices)),
            )
        else:
            propostas_unicas[chave_snapshot] = (titulo, snapshot, list(indices))

    # Uma evidência parcial que apenas confirma campos de uma versão atômica
    # completa não é uma segunda versão. Ela continua disponível e será
    # ligada somente aos campos que efetivamente corrobora. Sem este filtro,
    # uma NF completa + um extrato que confirma apenas o valor apareciam como
    # duas opções, tornando a revisão mais confusa e superestimando a dúvida.
    obrigatorios = set(campos_obrigatorios)
    ancoras_completas = [
        snapshot
        for _, snapshot, _ in propostas_unicas.values()
        if obrigatorios and obrigatorios.issubset(snapshot)
    ]
    if ancoras_completas:
        propostas_unicas = {
            chave: proposta
            for chave, proposta in propostas_unicas.items()
            if obrigatorios.issubset(proposta[1])
        }

    saida = []
    for titulo, snapshot, indices in propostas_unicas.values():
        grupo_nao_verificado = any(
            (
                candidatos[indice].get("_correlator_grupo_atomico")
                or candidatos[indice].get("versao")
                or candidatos[indice].get("alternativa")
                or candidatos[indice].get("grupo_correlacao")
            ) not in (None, "")
            and candidatos[indice].get("_correlator_grupo_verificado") is not True
            for indice in indices
        )
        confianca_campos: dict[str, Any] = {}
        for campo, valor in snapshot.items():
            opcoes = (confianca.get(campo) or {}).get("alternativas") or []
            opcao = next(
                (item for item in opcoes if _hash(item.get("valor")) == _hash(valor)),
                None,
            )
            if opcao is None:
                raise ValueError("alternativa_sem_evidencia_verificavel")
            confianca_campos[campo] = {
                "classificacao": opcao["classificacao"],
                "confianca": opcao["confianca"],
                "regra_id": opcao["regra_id"],
                "regra_version": opcao["regra_version"],
                "policy_version": _texto(policy_version),
                "avaliador": opcao["avaliador"],
                "ruleset_hash": opcao["ruleset_hash"],
                "inputs_hash": opcao["inputs_hash"],
                "inputs_contexto": opcao["inputs_contexto"],
                "inputs_canonico": opcao["inputs_canonico"],
                "linhagens": opcao["linhagens"],
                "penalidades": opcao["penalidades"],
                "caps": opcao["caps"],
            }
            if grupo_nao_verificado:
                confianca_campos[campo]["classificacao"] = min(
                    confianca_campos[campo]["classificacao"],
                    "possivel", key=lambda nivel: NIVEIS[nivel],
                )
                confianca_campos[campo]["confianca"] = min(
                    confianca_campos[campo]["confianca"], SCORES["possivel"]
                )
                confianca_campos[campo]["caps"] = sorted(set(
                    confianca_campos[campo]["caps"]
                    + ["grupo_correlacao_nao_verificado"]
                ))
                contexto_base = dict(
                    confianca_campos[campo]["inputs_contexto"]["base"]
                )
                contexto_base["grupo_correlacao_verificado"] = False
                _selar_contexto_confianca(
                    confianca_campos[campo], contexto_base,
                )
        niveis = [item["classificacao"] for item in confianca_campos.values()]
        nivel_geral = min(niveis, key=lambda nivel: NIVEIS[nivel]) if niveis else "inconclusivo"
        classificacao = nivel_geral if nivel_geral != "inconclusivo" else "ambiguo"
        confianca_geral = min(
            (item["confianca"] for item in confianca_campos.values()),
            default=0.0,
        )
        ausentes = sorted(set(campos_obrigatorios) - set(confianca_campos))
        if ausentes:
            confianca_geral = 0.0
            classificacao = "ambiguo"
        elif grupo_nao_verificado:
            classificacao = "possivel"
            confianca_geral = min(confianca_geral, SCORES["possivel"])
        saida.append({
            "titulo": sanitizar_payload(titulo, proteger_identificadores=True),
            "campos_snapshot": sanitizar_payload(
                snapshot, proteger_identificadores=True,
            ),
            "confianca_campos": confianca_campos,
            "confianca_geral": confianca_geral,
            "classificacao": classificacao,
            "regra_confianca_version": VERSAO_REGRA_CONFIANCA,
            "campos_obrigatorios_ausentes": ausentes,
            "indices_favoraveis": indices,
        })
    return saida


def _registros_evidencias_e_alternativas(
    candidatos: Sequence[Mapping[str, Any]],
    *,
    investigacao_id: str,
    tarefa_por_fonte: Mapping[str, str],
    tarefa_sintese_id: str,
    chave_investigacao: str,
    adaptador: str,
    confianca: Mapping[str, Any],
    policy_version: str,
    campos_obrigatorios: Sequence[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    evidencias: list[dict[str, Any]] = []
    evidencia_por_indice: dict[int, str] = {}
    evidencia_tarefa_por_indice: dict[int, str] = {}
    evidencia_por_chave: dict[str, str] = {}
    registro_por_chave: dict[str, dict[str, Any]] = {}
    for indice, candidato in enumerate(candidatos):
        campo = str(candidato.get("campo") or "").strip()
        if not campo or candidato.get("valor") in (None, ""):
            continue
        if (
            _chave_contem_segredo(campo)
            or campo.casefold() in CHAVES_TECNICAS_FRONTEND
            or _chave_identificadora_publica(campo)
        ):
            raise ValueError("campo_evidencia_proibido")
        linhagem = _linhagem_opaca(
            candidato.get("linhagem") or candidato.get("fonte") or adaptador
        )
        # A identidade usada para agrupar os fatos precisa ser exatamente a
        # mesma que selou o grupo atômico no correlator. Caso contrário, dois
        # campos do mesmo registro estruturado virariam evidências parciais e a
        # alternativa multi-campo não teria um único apoio coerente.
        natural = (
            candidato.get("_correlator_grupo_atomico")
            or candidato.get("chave_natural")
            or candidato.get("fonte_registro_id")
            or candidato.get("referencia")
            or {
            "linhagem": linhagem,
            "campo": campo,
            "valor": candidato.get("valor"),
            }
        )
        natural_hash = _hash(natural)
        # A chave natural é a identidade da evidência. Não acrescentar a
        # linhagem aqui impede que duas leituras conflitantes do mesmo
        # documento escapem como registros independentes e depois sejam
        # fundidas silenciosamente.
        tarefa_id = tarefa_por_fonte[str(candidato["_chave_fonte"])]
        chave_evidencia = chave_estavel("evi", tarefa_id, natural_hash)
        evidencia_id_logico = evidencia_por_chave.get(chave_evidencia)
        fatos = sanitizar_payload(
            {campo: candidato.get("valor")}, proteger_identificadores=True,
        )
        prova_campo = _prova_campo(candidato)
        if evidencia_id_logico is None:
            evidencia_id_logico = id_deterministico("evidencia", chave_evidencia)
            evidencia_por_chave[chave_evidencia] = evidencia_id_logico
            nivel = _nivel_evidencia(candidato)
            referencia = candidato.get("referencia") or candidato.get("referencia_opaca") or natural
            registro = {
                "id_logico": evidencia_id_logico,
                "investigacao_id": investigacao_id,
                "tarefa_id": tarefa_id,
                "fonte_tipo": _fonte_tipo(candidato, _texto(candidato.get("_adaptador") or adaptador)),
                "fonte_tabela": candidato.get("_fonte_tabela"),
                "fonte_registro_id": candidato.get("_fonte_registro_id"),
                "registro_origem_ref": candidato.get(
                    "_registro_origem_ref_observado"
                ),
                "snapshot_fonte_ref": candidato.get(
                    "_snapshot_fonte_ref_observado"
                ),
                "linhagem": linhagem,
                "chave_natural_hash": natural_hash,
                "referencia_opaca": chave_estavel("ref", referencia),
                "fatos_normalizados": fatos,
                "_provas_campos": {campo: prova_campo},
                "classificacao": nivel,
                "confianca": SCORES[nivel],
                "regra_confianca_version": VERSAO_REGRA_CONFIANCA,
                "resumo_sanitizado": sanitizar_payload(
                    candidato.get("evidencia") or candidato.get("resumo")
                    or f"Pista encontrada em {_fonte_tipo(candidato, _texto(candidato.get('_adaptador') or adaptador))}.",
                    proteger_identificadores=True,
                ),
            }
            evidencias.append(registro)
            registro_por_chave[chave_evidencia] = registro
        else:
            registro = registro_por_chave[chave_evidencia]
            if registro["linhagem"] != linhagem:
                raise ValueError("proveniencia_evidencia_incompativel")
            if (
                registro.get("fonte_tabela") != candidato.get("_fonte_tabela")
                or registro.get("fonte_registro_id")
                    != candidato.get("_fonte_registro_id")
                or registro.get("registro_origem_ref")
                    != candidato.get("_registro_origem_ref_observado")
                or registro.get("snapshot_fonte_ref")
                    != candidato.get("_snapshot_fonte_ref_observado")
            ):
                raise ValueError("origem_registro_evidencia_incompativel")
            anterior = registro["fatos_normalizados"].get(campo)
            if anterior is not None and _hash(anterior) != _hash(fatos[campo]):
                raise ValueError("fatos_evidencia_conflitantes")
            prova_anterior = registro["_provas_campos"].get(campo)
            if prova_anterior is not None and prova_anterior != prova_campo:
                raise ValueError("prova_campo_evidencia_conflitante")
            registro["fatos_normalizados"][campo] = fatos[campo]
            registro["_provas_campos"][campo] = prova_campo
            nivel = _nivel_evidencia(candidato)
            if NIVEIS[nivel] > NIVEIS[registro["classificacao"]]:
                registro["classificacao"] = nivel
                registro["confianca"] = SCORES[nivel]
        evidencia_por_indice[indice] = evidencia_id_logico
        evidencia_tarefa_por_indice[indice] = registro["tarefa_id"]

    for evidencia in evidencias:
        provas = {
            "versao": VERSAO_PROVAS_CAMPOS,
            "campos": evidencia.pop("_provas_campos"),
        }
        canonico = _json_canonico(provas)
        evidencia["provas_campos"] = provas
        evidencia["provas_campos_canonico"] = canonico
        evidencia["provas_campos_hash"] = hashlib.sha256(
            canonico.encode("utf-8")
        ).hexdigest()

    alternativas: list[dict[str, Any]] = []
    ligacoes: list[dict[str, Any]] = []
    registros_por_id = {item["id_logico"]: item for item in evidencias}
    for numero, proposta in enumerate(_alternativas_planejadas(
        candidatos,
        confianca,
        policy_version=policy_version,
        campos_obrigatorios=campos_obrigatorios,
    ), 1):
        chave_alternativa = chave_estavel(
            "alt", chave_investigacao, proposta["campos_snapshot"]
        )
        alternativa_id_logico = id_deterministico("alternativa", chave_alternativa)
        indices_favoraveis = set(proposta.pop("indices_favoraveis"))
        alternativas.append({
            "id_logico": alternativa_id_logico,
            "investigacao_id": investigacao_id,
            "tarefa_id": tarefa_sintese_id,
            "chave_idempotencia": chave_alternativa,
            "titulo": proposta["titulo"] or f"Opção {numero}",
            "campos_snapshot": proposta["campos_snapshot"],
            "confianca_campos": proposta["confianca_campos"],
            "confianca_geral": proposta["confianca_geral"],
            "classificacao": proposta["classificacao"],
            "regra_confianca_version": proposta["regra_confianca_version"],
            "justificativa_sanitizada": (
                "Esta opção reúne apenas pistas compatíveis; diferenças e campos "
                "ausentes continuam visíveis para conferência."
            ),
            "origem_modelo": any(
                _texto(candidatos[i].get("tipo_correspondencia")) == "extracao_llm"
                for i in indices_favoraveis
            ),
        })
        referencias_evidencia = sorted({
            (
                evidencia_por_indice[indice],
                evidencia_tarefa_por_indice[indice],
            )
            for indice in range(len(candidatos))
            if indice in evidencia_por_indice
        })
        for evidencia_id_logico, evidencia_tarefa_id in referencias_evidencia:
            fatos = registros_por_id[evidencia_id_logico]["fatos_normalizados"]
            campos_comuns = set(fatos) & set(proposta["campos_snapshot"])
            if not campos_comuns:
                continue
            campos_favoraveis = sorted(
                campo for campo in campos_comuns
                if _hash(fatos[campo]) == _hash(
                    proposta["campos_snapshot"][campo]
                )
            )
            campos_contrarios = sorted(
                campo for campo in campos_comuns
                if _hash(fatos[campo]) != _hash(
                    proposta["campos_snapshot"][campo]
                )
            )
            contraria = bool(campos_contrarios)
            favoravel = bool(campos_favoraveis) and not contraria
            if not favoravel and not contraria:
                continue
            ligacao = {
                "investigacao_id": investigacao_id,
                "alternativa_id_logico": alternativa_id_logico,
                "evidencia_id_logico": evidencia_id_logico,
                "evidencia_tarefa_id": evidencia_tarefa_id,
                "papel": "favoravel" if favoravel else "contraria",
                "campos_suportados": campos_favoraveis if favoravel else [],
                "campos_contestados": campos_contrarios if contraria else [],
            }
            ligacoes.append(ligacao)
    return evidencias, alternativas, ligacoes


def _pendencias_planejadas(
    *,
    investigacao_id: str,
    tarefa_sintese_id: str,
    chave_investigacao: str,
    resultado: str,
    cobertura: str,
    confianca: Mapping[str, Any],
    campos_obrigatorios: Sequence[str],
    campos_ausentes_alternativas: Sequence[str] = (),
) -> list[dict[str, Any]]:
    propostas: list[tuple[str, str | None, str]] = []
    for campo in campos_obrigatorios:
        if campo not in confianca:
            propostas.append(("dado_ausente", campo, f"Falta confirmar {campo}."))
    for campo in campos_ausentes_alternativas:
        if not any(item[0] == "dado_ausente" and item[1] == campo for item in propostas):
            propostas.append((
                "dado_ausente", campo,
                f"Uma ou mais versões ainda não confirmam {campo}.",
            ))
    if cobertura == "reautenticacao_necessaria":
        propostas.append(("reautenticacao", None, "A fonte precisa ser reconectada antes de concluir."))
    elif cobertura in {"indisponivel", "erro_permanente"}:
        propostas.append(("fonte_indisponivel", None, "Uma fonte necessária não pôde ser consultada."))
    elif cobertura == "cobertura_incompleta":
        propostas.append(("cobertura_incompleta", None, "A busca não cobriu todo o período ou contexto necessário."))
    if resultado == "alternativas_multiplas":
        propostas.append(("decisao_humana", None, "Há mais de uma versão plausível; escolha ou corrija a mais provável."))
    elif resultado == "divergente":
        propostas.append(("divergencia", None, "As evidências centrais divergem e precisam de conferência."))
    elif resultado == "evidencia_insuficiente" and not propostas:
        propostas.append(("dado_ausente", None, "Ainda faltam evidências verificáveis para sugerir uma versão."))

    saida = []
    for tipo, campo, descricao in propostas:
        chave = chave_estavel("pen", chave_investigacao, tipo, campo)
        saida.append({
            "id_logico": id_deterministico("pendencia", chave),
            "investigacao_id": investigacao_id,
            "tarefa_id": tarefa_sintese_id,
            "chave_idempotencia": chave,
            "tipo": tipo,
            "campo": campo,
            "descricao_sanitizada": descricao,
            "estado": "aberta",
        })
    return saida


def planejar_investigacao(
    assunto: Mapping[str, Any] | str,
    origem: Mapping[str, Any],
    consulta: Mapping[str, Any] | str,
    *,
    fingerprint_base: str,
    cobertura: str,
    instante_referencia: str,
    candidatos: Iterable[Mapping[str, Any]] = (),
    campos_obrigatorios: Sequence[str] = (),
    versao_politica: str = VERSAO_POLITICA_PADRAO,
    adaptador: str = "outro",
    versao_adaptador: str = "v1",
    fingerprint_atual: str | None = None,
    fontes: Iterable[FonteAdaptadorSelada] | None = None,
    source_draft_id: str | None = None,
    source_draft_atualizado_em: str | None = None,
    negocio_candidato_id: str | None = None,
    source_candidato_atualizado_em: str | None = None,
    negocio_candidato_ids: Sequence[str] | None = None,
    source_candidatos_atualizados_em: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if cobertura not in ESTADOS_COBERTURA:
        raise ValueError("cobertura_invalida")
    versao_politica_n = _texto(versao_politica)
    versao_adaptador_n = _texto(versao_adaptador)
    if not versao_politica_n:
        raise ValueError("versao_politica_invalida")
    if not versao_adaptador_n:
        raise ValueError("versao_adaptador_invalida")
    assunto_n = normalizar_assunto(assunto)
    instante = _validar_instante(instante_referencia)
    source_draft_id_n = (
        _uuid_canonico(source_draft_id, "source_draft_id_invalido")
        if source_draft_id is not None else None
    )
    negocio_candidato_id_n = (
        _uuid_canonico(negocio_candidato_id, "negocio_candidato_id_invalido")
        if negocio_candidato_id is not None else None
    )
    if source_draft_id_n is not None and source_draft_atualizado_em is None:
        raise ValueError("source_draft_timestamp_obrigatorio")
    source_draft_atualizado_em_n = (
        _validar_instante(source_draft_atualizado_em)
        if source_draft_atualizado_em is not None else None
    )
    candidatos_ids = {
        _uuid_canonico(valor, "negocio_candidato_ids_invalido")
        for valor in (negocio_candidato_ids or ()) if valor not in (None, "")
    }
    if negocio_candidato_id_n is not None:
        candidatos_ids.add(negocio_candidato_id_n)
    candidatos_ids_ordenados = sorted(candidatos_ids)
    if source_draft_id_n is not None and candidatos_ids_ordenados:
        raise ValueError("origem_mista_draft_candidato_bloqueada")
    timestamps_candidatos: dict[str, str] = {}
    for chave, valor in (source_candidatos_atualizados_em or {}).items():
        chave_n = _uuid_canonico(chave, "source_candidatos_id_invalido")
        valor_n = _validar_instante(valor)
        if chave_n in timestamps_candidatos and timestamps_candidatos[chave_n] != valor_n:
            raise ValueError("source_candidato_timestamp_conflitante")
        timestamps_candidatos[chave_n] = valor_n
    source_candidato_atualizado_em_n = (
        _validar_instante(source_candidato_atualizado_em)
        if source_candidato_atualizado_em is not None else None
    )
    if negocio_candidato_id_n is not None and source_candidato_atualizado_em_n is not None:
        anterior = timestamps_candidatos.setdefault(
            negocio_candidato_id_n, source_candidato_atualizado_em_n
        )
        if anterior != source_candidato_atualizado_em_n:
            raise ValueError("source_candidato_timestamp_conflitante")
    if source_candidato_atualizado_em_n is not None and negocio_candidato_id_n is None:
        raise ValueError("source_candidato_sem_id")
    if set(timestamps_candidatos) != set(candidatos_ids_ordenados):
        raise ValueError("source_candidatos_timestamps_obrigatorios")
    candidato_principal = (
        negocio_candidato_id_n
        if negocio_candidato_id_n is not None
        else (candidatos_ids_ordenados[0] if candidatos_ids_ordenados else None)
    )
    timestamp_principal = (
        timestamps_candidatos.get(candidato_principal)
        if candidato_principal is not None else None
    )
    adaptador_n = _texto(adaptador)
    if adaptador_n not in ADAPTADORES_FONTES:
        raise ValueError("adaptador_invalido")
    if fontes is not None:
        fontes_recebidas = list(fontes)
        if any(not isinstance(fonte, FonteAdaptadorSelada) for fonte in fontes_recebidas):
            raise ValueError("fontes_exigem_envelope_selado")
        fontes_brutas = [fonte._dados_confiaveis() for fonte in fontes_recebidas]
    else:
        # Compatibilidade de sombra: fatos diretos continuam planejáveis, mas
        # não podem provar cobertura, linhagem independente ou identidade exata.
        fontes_brutas = [{
            "_fonte_selada": False,
            "adaptador": adaptador_n,
            "versao_adaptador": versao_adaptador_n,
            "consulta": consulta,
            "cobertura": "cobertura_incompleta",
            "linhagem": normalizar_origem(origem).get("linhagem") or "nao-selada",
            "candidatos": list(candidatos),
        }]
    if not fontes_brutas:
        raise ValueError("fontes_vazias")
    fontes_normalizadas: list[dict[str, Any]] = []
    for fonte in fontes_brutas:
        item = dict(fonte)
        fonte_selada = item.pop("_fonte_selada", False) is True
        if not fonte_selada and fontes is not None:
            raise ValueError("fonte_selada_invalida")
        adaptador_fonte = _texto(item.get("adaptador") or adaptador_n)
        cobertura_fonte = _texto(item.get("cobertura") or "cobertura_incompleta")
        if adaptador_fonte not in ADAPTADORES_FONTES:
            raise ValueError("adaptador_invalido")
        if cobertura_fonte not in ESTADOS_COBERTURA:
            raise ValueError("cobertura_invalida")
        consulta_fonte = item.get("consulta") or consulta
        chave_fonte = str(item.get("chave_fonte") or "") if fonte_selada else chave_estavel(
            "fonte-nao-selada", normalizar_consulta(consulta_fonte),
            _texto(item.get("linhagem") or ""),
        )
        if not re.fullmatch(r"fonte(?:-nao-selada)?_[0-9a-f]{32}", chave_fonte):
            raise ValueError("chave_fonte_invalida")
        candidatos_fonte = []
        for candidato in item.get("candidatos", ()):
            candidato_n = dict(candidato)
            internos_permitidos = {
                "_adaptador", "_correlator_grupo_verificado",
                "_correlator_grupo_atomico", "_correlator_titulo_atomico",
                "_identidade_exata_tipo", "_identidade_exata_namespace_hash",
                "_identidade_exata_valor_hash", "_familia_fonte",
                "_fonte_tabela", "_fonte_registro_id", "_origem_classe",
                "_autoridade_fonte", "_dataset_chave",
                "_registro_origem_chave", "_ancestral_chave",
                "_registro_origem_ref_observado",
                "_snapshot_fonte_ref_observado",
            }
            if any(
                str(chave).casefold() in SELOS_CONFIANCA_RESERVADOS
                or (
                    str(chave).startswith("_")
                    and (not fonte_selada or str(chave) not in internos_permitidos)
                )
                for chave in candidato_n
            ):
                raise ValueError("selo_confianca_reservado_ao_correlator")
            campo_n = _normalizar_chave(candidato_n.get("campo"))
            tipo_declarado = _texto(
                candidato_n.get("tipo_correspondencia")
                or candidato_n.get("criterio") or "extracao_llm"
            )
            identidade_interna = (
                _identidade_exata_interna(candidato_n) if fonte_selada else None
            )
            # O modelo fornece fatos, nunca o selo. Até um nome de campo como
            # `chave_nfe` é apenas texto sem a identidade opaca do adaptador.
            if (
                fonte_selada
                and
                adaptador_fonte in ADAPTADORES_ESTRUTURADOS
                and identidade_interna is not None
                and tipo_declarado in {
                    "identificador_exato", "documento_referenciado"
                }
            ):
                tipo_derivado = tipo_declarado
            elif tipo_declarado in {"ocr", "extracao_llm", "nome", "valor"}:
                tipo_derivado = tipo_declarado
            else:
                tipo_derivado = "extracao_llm"
            candidato_n["campo"] = campo_n
            candidato_n["tipo_correspondencia"] = tipo_derivado
            if not fonte_selada:
                for chave_reservada in tuple(candidato_n):
                    if str(chave_reservada).startswith("_"):
                        candidato_n.pop(chave_reservada, None)
            for sinal_nao_confiavel in (
                "extracao_confirmada", "aritmetica_consistente",
                "divergencia_central",
            ):
                candidato_n.pop(sinal_nao_confiavel, None)
            candidato_n["linhagem"] = item.get("linhagem") or "nao-selada"
            candidato_n["_adaptador"] = adaptador_fonte
            candidato_n["_chave_fonte"] = chave_fonte
            if not fonte_selada:
                candidato_n["_correlator_grupo_verificado"] = False
                candidato_n.pop("chave_natural", None)
                candidato_n.pop("fonte_registro_id", None)
                for chave_identidade in CAMPOS_IDENTIFICADORES_EXATOS:
                    candidato_n.pop(chave_identidade, None)
            candidatos_fonte.append(candidato_n)
        if cobertura_fonte == "completa" and not candidatos_fonte:
            cobertura_fonte = "vazio_com_cobertura"
        if cobertura_fonte == "vazio_com_cobertura" and candidatos_fonte:
            raise ValueError("fonte_vazia_com_resultados_invalida")
        fontes_normalizadas.append({
            "adaptador": adaptador_fonte,
            "versao_adaptador": _texto(
                item.get("versao_adaptador")
                if item.get("versao_adaptador") is not None
                else versao_adaptador_n
            ),
            "consulta": consulta_fonte,
            "cobertura": cobertura_fonte,
            "chave_fonte": chave_fonte,
            "prova_cobertura": (
                dict(item.get("prova_cobertura") or {}) if fonte_selada else {}
            ),
            "candidatos": candidatos_fonte,
        })
    if len({item["chave_fonte"] for item in fontes_normalizadas}) != len(fontes_normalizadas):
        raise ValueError("fonte_duplicada")
    campos_solicitados = sorted({
        _normalizar_chave(campo) for campo in campos_obrigatorios
        if _normalizar_chave(campo)
    })
    campos_obrigatorios_n = campos_politica_assunto(
        assunto_n["tipo"], versao_politica_n,
    )
    if campos_solicitados and campos_solicitados != campos_obrigatorios_n:
        raise ValueError("campos_obrigatorios_nao_derivados_da_politica")
    if any(
        not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", campo)
        for campo in campos_obrigatorios_n
    ):
        raise ValueError("campo_obrigatorio_invalido")
    if any(not fonte["versao_adaptador"] for fonte in fontes_normalizadas):
        raise ValueError("versao_adaptador_invalida")
    consulta_sintese = {
        "tipo": "sintese",
        "pergunta": "sintetizar evidencias aceitas",
        "termos": [],
        "campos": campos_obrigatorios_n,
        "janela_inicio": "",
        "janela_fim": "",
        "limite": 100,
        "paginacao": "inicio",
        "cobertura_esperada": "fontes_planejadas",
    }
    plano_tarefas: list[dict[str, Any]] = []
    for fonte in fontes_normalizadas:
        consulta_canonica = normalizar_consulta(fonte["consulta"])
        contrato = contrato_consulta(consulta_canonica)
        item_ref = chave_estavel(
            "pitem", fonte["adaptador"], fonte["versao_adaptador"],
            fonte["chave_fonte"], consulta_canonica,
        )
        fonte["plano_item_ref"] = item_ref
        plano_tarefas.append({
            "plano_item_ref": item_ref,
            "adaptador": fonte["adaptador"],
            "adaptador_version": fonte["versao_adaptador"],
            **contrato,
        })
    contrato_sintese = contrato_consulta(consulta_sintese)
    sintese_item_ref = chave_estavel(
        "pitem", "sintese", versao_politica_n, consulta_sintese,
    )
    plano_tarefas.append({
        "plano_item_ref": sintese_item_ref,
        "adaptador": "sintese",
        "adaptador_version": versao_politica_n,
        **contrato_sintese,
    })
    plano_tarefas = sorted(plano_tarefas, key=lambda item: item["plano_item_ref"])
    plano_contexto = {
        "tarefas": plano_tarefas,
        "campos_obrigatorios": campos_obrigatorios_n,
        "policy_schema_hash": HASH_SCHEMA_POLITICAS,
    }
    plano_canonico = _json_canonico(plano_contexto)
    plano_hash = hashlib.sha256(plano_canonico.encode("utf-8")).hexdigest()
    candidatos_lista = sorted(
        (candidato for fonte in fontes_normalizadas for candidato in fonte["candidatos"]),
        key=lambda candidato: json.dumps(
            _canon(candidato), ensure_ascii=False, sort_keys=True, default=str
        ),
    )
    coberturas_por_fonte = {
        fonte["chave_fonte"]: fonte["cobertura"]
        for fonte in fontes_normalizadas
    }
    candidatos_lista = _correlacionar_candidatos(
        candidatos_lista,
        cobertura_por_fonte=coberturas_por_fonte,
    )
    if fingerprint_atual is not None and not re.fullmatch(
        r"[0-9a-f]{64}", fingerprint_atual
    ):
        raise ValueError("fingerprint_atual_invalido")
    if source_draft_id_n is not None:
        vinculo_assunto = {
            "tipo": "draft",
            "id": source_draft_id_n,
            "snapshot": source_draft_atualizado_em_n,
        }
    elif candidatos_ids_ordenados:
        vinculo_assunto = {
            "tipo": "candidatos",
            "ids": candidatos_ids_ordenados,
            "snapshots": timestamps_candidatos,
        }
    else:
        vinculo_assunto = {"tipo": "sem_vinculo"}
    chaves = chaves_investigacao(
        assunto, origem, consulta,
        fingerprint_base=fingerprint_base,
        versao_politica=versao_politica_n,
        adaptador=adaptador_n,
        versao_adaptador=versao_adaptador_n,
        plano_hash=plano_hash,
        vinculo_assunto=vinculo_assunto,
    )
    confianca = confianca_explicavel(
        candidatos_lista,
        policy_version=versao_politica_n,
        cobertura_por_fonte=coberturas_por_fonte,
    )
    coberturas_fontes = [fonte["cobertura"] for fonte in fontes_normalizadas]
    if any(
        estado not in {"completa", "vazio_com_cobertura"}
        for estado in coberturas_fontes
    ):
        cobertura_geral = "cobertura_incompleta"
    elif all(estado == "vazio_com_cobertura" for estado in coberturas_fontes):
        cobertura_geral = "vazio_com_cobertura"
    else:
        cobertura_geral = "completa"
    obsoleto = bool(fingerprint_atual and fingerprint_atual != fingerprint_base)
    resultado = resultado_investigacao(
        confianca,
        cobertura=cobertura_geral,
        campos_obrigatorios=campos_obrigatorios_n,
        fingerprint_obsoleto=obsoleto,
    )
    origem_n = normalizar_origem(origem)
    consulta_n = normalizar_consulta(consulta)
    investigacao_id = id_deterministico("investigacao", chaves["investigacao"])
    estado_execucao = "obsoleta" if obsoleto else "concluida"
    tarefas: list[dict[str, Any]] = []
    tarefa_por_fonte: dict[str, str] = {}
    for fonte in fontes_normalizadas:
        chave_tarefa = chave_estavel(
            "tar", chaves["investigacao"], fonte["adaptador"],
            normalizar_consulta(fonte["consulta"]), fonte["versao_adaptador"], fonte["chave_fonte"],
        )
        tarefa_id = id_deterministico("tarefa", chave_tarefa)
        tarefa_por_fonte[fonte["chave_fonte"]] = tarefa_id
        consulta_canonica = normalizar_consulta(fonte["consulta"])
        contrato = contrato_consulta(consulta_canonica)
        tarefas.append({
            "id": tarefa_id, "investigacao_id": investigacao_id,
            "chave_idempotencia": chave_tarefa, "adaptador": fonte["adaptador"],
            "plano_item_ref": fonte["plano_item_ref"],
            **contrato,
            "adaptador_version": fonte["versao_adaptador"],
            "estado_execucao": estado_execucao,
            "estado_resultado": (
                "evidencia_insuficiente"
                if fonte["cobertura"] in {"completa", "vazio_com_cobertura"}
                else "cobertura_incompleta"
            ),
            "estado_cobertura": fonte["cobertura"],
            # Prévia lógica para testes/dry-run. A publicação real exige a
            # mesma prova assinada com a chave do adaptador e vinculada ao
            # lease/fencing/bundle pelo broker fora do processo de síntese.
            "prova_cobertura": fonte["prova_cobertura"],
            "concluido_em": instante if estado_execucao == "concluida" else None,
            "criado_em": instante,
        })
    chave_sintese = chave_estavel(
        "tar", chaves["investigacao"], "sintese", versao_politica_n
    )
    tarefa_sintese_id = id_deterministico("tarefa", chave_sintese)
    tarefas.append({
        "id": tarefa_sintese_id, "investigacao_id": investigacao_id,
        "chave_idempotencia": chave_sintese, "adaptador": "sintese",
        "plano_item_ref": sintese_item_ref,
        **contrato_sintese,
        "adaptador_version": versao_politica_n,
        "estado_execucao": estado_execucao, "estado_resultado": resultado,
        "estado_cobertura": cobertura_geral,
        "concluido_em": instante if estado_execucao == "concluida" else None,
        "criado_em": instante,
    })
    evidencias, alternativas, ligacoes = _registros_evidencias_e_alternativas(
        candidatos_lista,
        investigacao_id=investigacao_id,
        tarefa_por_fonte=tarefa_por_fonte,
        tarefa_sintese_id=tarefa_sintese_id,
        chave_investigacao=chaves["investigacao"],
        adaptador=adaptador_n,
        confianca=confianca,
        policy_version=versao_politica_n,
        campos_obrigatorios=campos_obrigatorios_n,
    )
    cobertura_confirmada = cobertura_geral in {
        "completa", "vazio_com_cobertura",
    }
    if not cobertura_confirmada:
        for alternativa in alternativas:
            confianca_geral = float(alternativa.get("confianca_geral") or 0)
            if confianca_geral > SCORES["possivel"]:
                alternativa["confianca_geral"] = SCORES["possivel"]
                alternativa["classificacao"] = "possivel"
    campos_obrigatorios_set = {
        _texto(campo) for campo in campos_obrigatorios_n if _texto(campo)
    }
    ausentes_por_alternativa = [
        campos_obrigatorios_set - set(alternativa.get("campos_snapshot") or {})
        for alternativa in alternativas
    ]
    campos_ausentes_alternativas = sorted(set().union(*ausentes_por_alternativa)) \
        if ausentes_por_alternativa else sorted(campos_obrigatorios_set)
    alternativas_completas = [
        alternativa for alternativa, ausentes in zip(
            alternativas, ausentes_por_alternativa
        ) if not ausentes
    ]
    ha_contraprova = any(
        ligacao.get("papel") == "contraria" for ligacao in ligacoes
    )
    if not cobertura_confirmada:
        resultado = "cobertura_incompleta"
    elif not alternativas_completas:
        # Fragmentos de fontes distintas nunca são fundidos para fabricar uma
        # versão. Eles permanecem visíveis como pistas, mas o resultado segue
        # incompleto até existir uma evidência atômica que cubra a opção.
        resultado = "evidencia_insuficiente"
    elif len(alternativas_completas) == 1:
        if ha_contraprova:
            resultado = "divergente"
        elif alternativas_completas[0].get("classificacao") in {
            "ambiguo", "possivel"
        }:
            resultado = "evidencia_insuficiente"
        else:
            resultado = "alternativa_unica"
    else:
        resultado = "divergente" if ha_contraprova else "alternativas_multiplas"
    tarefas[-1]["estado_resultado"] = resultado
    evento_id = id_deterministico(
        "evento_investigacao",
        chave_estavel("evt", chaves["investigacao"], resultado),
    )
    pendencias = _pendencias_planejadas(
        investigacao_id=investigacao_id,
        tarefa_sintese_id=tarefa_sintese_id,
        chave_investigacao=chaves["investigacao"],
        resultado=resultado,
        cobertura=cobertura_geral,
        confianca=confianca,
        campos_obrigatorios=campos_obrigatorios_n,
        campos_ausentes_alternativas=campos_ausentes_alternativas,
    )
    # A cobertura global fica incompleta, mas a causa por fonte não pode se
    # perder: sucessos continuam como evidência e cada falha vira pendência
    # tipificada própria.
    tipo_pendencia_fonte = {
        "reautenticacao_necessaria": "reautenticacao",
        "indisponivel": "fonte_indisponivel",
        "erro_permanente": "fonte_indisponivel",
        "cobertura_incompleta": "cobertura_incompleta",
    }
    for fonte in fontes_normalizadas:
        tipo = tipo_pendencia_fonte.get(fonte["cobertura"])
        if tipo is None:
            continue
        chave = chave_estavel(
            "pen", chaves["investigacao"], tipo, fonte["adaptador"],
            fonte["chave_fonte"],
        )
        pendencias.append({
            "id_logico": id_deterministico("pendencia", chave),
            "investigacao_id": investigacao_id,
            "tarefa_id": tarefa_sintese_id,
            "chave_idempotencia": chave,
            "tipo": tipo,
            "campo": None,
            "fonte_tipo": fonte["adaptador"],
            "descricao_sanitizada": f"Cobertura pendente na fonte {fonte['adaptador']}.",
            "estado": "aberta",
        })

    registros = {
        "investigacoes_revisao": [{
            "id": investigacao_id,
            "chave_idempotencia": chaves["investigacao"],
            "assunto_tipo": assunto_n["tipo"],
            "assunto_referencia": assunto_n["referencia"] or None,
            "titulo": assunto_n["titulo"] or "Conferir evidências disponíveis",
            "source_draft_id": source_draft_id_n,
            "source_draft_atualizado_em": source_draft_atualizado_em_n,
            "negocio_candidato_id": candidato_principal,
            "source_candidato_atualizado_em": timestamp_principal,
            "negocio_candidato_ids": candidatos_ids_ordenados,
            "source_candidatos_atualizados_em": timestamps_candidatos,
            "contexto_nome": assunto_n["contexto_nome"] or None,
            "origem_canal": origem_n["canal"],
            "origem_conversa_id": origem_n.get("conversa_id"),
            "origem_mensagem_id": origem_n.get("mensagem_id"),
            "fingerprint_base": fingerprint_base,
            "plano_hash": plano_hash,
            "plano_canonico": plano_canonico,
            "plano_tarefas": plano_tarefas,
            "policy_version": versao_politica_n,
            "policy_schema_hash": HASH_SCHEMA_POLITICAS,
            "campos_obrigatorios": campos_obrigatorios_n,
            "estado_execucao": estado_execucao,
            "estado_resultado": resultado,
            "concluida_em": instante if estado_execucao == "concluida" else None,
            "criado_em": instante,
            "atualizado_em": instante,
        }],
        "investigacao_tarefas": tarefas,
        "investigacao_evidencias": evidencias,
        "investigacao_alternativas": alternativas,
        "investigacao_alternativa_evidencias": ligacoes,
        "investigacao_pendencias": pendencias,
        "investigacao_eventos": [{
            "id": evento_id,
            "investigacao_id": investigacao_id,
            "chave_idempotencia": chave_estavel("evt", chaves["investigacao"], resultado),
            "tipo": "resultado_atualizado",
            "resumo_sanitizado": "Rodada planejada sem materialização da fila.",
            "criado_em": instante,
        }],
        # A entrega nasce atomicamente no trigger canônico do evento. O plano
        # lógico nunca tenta inserir a mesma relação uma segunda vez.
        "investigacao_entregas": [],
    }

    plano = {
        "modo": "dry_run",
        "nenhuma_escrita_executada": True,
        "tabelas_operacionais_alteradas": 0,
        "tabelas_controle_planejadas": sorted(TABELAS_CONTROLE),
        "materializador_canonico": "tools/materializar_revisoes_staging.py",
        "tabelas_anexo": sorted(TABELAS_ANEXO),
        "anexo_exige_rascunho_existente": True,
        "registros_resultado_sao_projecao_logica": True,
        "ids_resultado_sao_logicos": True,
        "publicador_atomico": "public.publicar_resultado_tarefa_investigacao",
        "dml_exige_binding_lease_fencing_ativo": True,
        "chaves": chaves,
        "resultado": resultado,
        "registros": sanitizar_payload(registros),
    }
    validar_plano(plano)
    return plano


def _contem_chave(valor: Any, chave_buscada: str) -> bool:
    if isinstance(valor, Mapping):
        return any(
            str(chave).casefold() == chave_buscada
            or _contem_chave(item, chave_buscada)
            for chave, item in valor.items()
        )
    if isinstance(valor, (list, tuple)):
        return any(_contem_chave(item, chave_buscada) for item in valor)
    return False


def _contem_destino_operacional(valor: Any) -> bool:
    chaves_destino = {
        "target_table", "tabela_destino", "destino_operacional",
        "insert_into", "update_table", "delete_from",
    }
    if isinstance(valor, Mapping):
        for chave, item in valor.items():
            if str(chave).casefold() in chaves_destino and _texto(item) in TABELAS_OPERACIONAIS:
                return True
            if _contem_destino_operacional(item):
                return True
    elif isinstance(valor, (list, tuple)):
        return any(_contem_destino_operacional(item) for item in valor)
    return False


def validar_plano(plano: Mapping[str, Any]) -> None:
    if plano.get("modo") != "dry_run" or plano.get("nenhuma_escrita_executada") is not True:
        raise ValueError("plano_nao_e_dry_run")
    tabelas_controle = set(plano.get("tabelas_controle_planejadas") or [])
    tabelas_anexo = set(plano.get("tabelas_anexo") or [])
    registros = set((plano.get("registros") or {}).keys())
    if tabelas_controle != TABELAS_CONTROLE or registros != TABELAS_CONTROLE:
        raise ValueError("allowlist_controle_invalida")
    if tabelas_anexo != TABELAS_ANEXO or plano.get("anexo_exige_rascunho_existente") is not True:
        raise ValueError("allowlist_anexo_invalida")
    if (
        plano.get("registros_resultado_sao_projecao_logica") is not True
        or plano.get("ids_resultado_sao_logicos") is not True
        or plano.get("dml_exige_binding_lease_fencing_ativo") is not True
        or plano.get("publicador_atomico")
        != "public.publicar_resultado_tarefa_investigacao"
    ):
        raise ValueError("binding_lease_fencing_obrigatorio")
    if (tabelas_controle | tabelas_anexo) & TABELAS_OPERACIONAIS:
        raise ValueError("tabela_operacional_bloqueada")
    if _contem_chave(plano, "target_table"):
        raise ValueError("target_table_bloqueada")
    if _contem_destino_operacional(plano):
        raise ValueError("destino_operacional_bloqueado")
    if any((plano.get("registros") or {}).get(tabela) for tabela in TABELAS_ANEXO):
        raise ValueError("anexo_so_pode_ocorrer_em_rascunho_existente")

    dados = plano.get("registros") or {}
    nomes_resultado = (
        "investigacao_evidencias", "investigacao_alternativas",
        "investigacao_pendencias",
    )
    for tabela in nomes_resultado:
        linhas = list(dados.get(tabela) or [])
        ids = [linha.get("id_logico") for linha in linhas]
        if any(not valor for valor in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"id_logico_invalido:{tabela}")
        if any("id" in linha for linha in linhas):
            raise ValueError(f"id_fisico_indevido:{tabela}")

    alternativas = list(dados.get("investigacao_alternativas") or [])
    evidencias = list(dados.get("investigacao_evidencias") or [])
    pendencias = list(dados.get("investigacao_pendencias") or [])
    ligacoes = list(dados.get("investigacao_alternativa_evidencias") or [])
    investigacoes = list(dados.get("investigacoes_revisao") or [])
    tarefas = list(dados.get("investigacao_tarefas") or [])
    if len(investigacoes) != 1:
        raise ValueError("investigacao_unica_obrigatoria")
    investigacao = investigacoes[0]
    try:
        plano_contexto = json.loads(investigacao["plano_canonico"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("plano_canonico_invalido") from exc
    campos_manifesto = (
        "plano_item_ref", "adaptador", "adaptador_version", "consulta_ref",
        "consulta_schema_version", "consulta_spec", "consulta_canonico",
        "consulta_hash",
    )
    manifesto_por_ref = {
        item.get("plano_item_ref"): item
        for item in (investigacao.get("plano_tarefas") or [])
    }
    tarefas_por_ref = {
        item.get("plano_item_ref"): item for item in tarefas
    }
    if (
        plano_contexto != {
            "tarefas": investigacao.get("plano_tarefas"),
            "campos_obrigatorios": investigacao.get("campos_obrigatorios"),
            "policy_schema_hash": investigacao.get("policy_schema_hash"),
        }
        or hashlib.sha256(
            investigacao["plano_canonico"].encode("utf-8")
        ).hexdigest() != investigacao.get("plano_hash")
        or set(tarefas_por_ref) != set(manifesto_por_ref)
        or investigacao.get("policy_schema_hash") != HASH_SCHEMA_POLITICAS
        or investigacao.get("campos_obrigatorios") != campos_politica_assunto(
            investigacao.get("assunto_tipo"),
            investigacao.get("policy_version"),
        )
        or sum(
            item.get("adaptador") == "sintese"
            for item in (investigacao.get("plano_tarefas") or [])
        ) != 1
        or any(
            any(
                tarefa.get(campo) != manifesto.get(campo)
                for campo in campos_manifesto
            )
            for referencia, manifesto in manifesto_por_ref.items()
            for tarefa in [tarefas_por_ref.get(referencia) or {}]
        )
    ):
        raise ValueError("plano_tarefas_incompativel")
    for tarefa in tarefas:
        resolver_consulta_tarefa(tarefa)
    campos_obrigatorios = {
        _texto(campo)
        for campo in (
            (investigacoes[0].get("campos_obrigatorios") or [])
            if investigacoes else []
        )
        if _texto(campo)
    }
    for alternativa in alternativas:
        for avaliacao in (alternativa.get("confianca_campos") or {}).values():
            try:
                contexto = json.loads(avaliacao["inputs_canonico"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("selo_confianca_invalido") from exc
            if (
                contexto != avaliacao.get("inputs_contexto")
                or hashlib.sha256(
                    avaliacao["inputs_canonico"].encode("utf-8")
                ).hexdigest() != avaliacao.get("inputs_hash")
                or avaliacao.get("ruleset_hash") != HASH_REGRAS_CONFIANCA
                or avaliacao.get("regra_version") != VERSAO_REGRA_CONFIANCA
                or avaliacao.get("policy_version") != _texto(
                    investigacao.get("policy_version")
                )
            ):
                raise ValueError("selo_confianca_invalido")
    ausentes_alternativas = (
        set().union(*(
            campos_obrigatorios - set(item.get("campos_snapshot") or {})
            for item in alternativas
        ))
        if alternativas else set(campos_obrigatorios)
    )
    campos_pendentes = {
        _texto(item.get("campo")) for item in pendencias if item.get("campo")
    }
    if ausentes_alternativas - campos_pendentes:
        raise ValueError("alternativa_parcial_sem_pendencia_explicita")
    alternativas_por_id = {item["id_logico"]: item for item in alternativas}
    alt_ids = set(alternativas_por_id)
    evidencias_por_ref = {
        (item["id_logico"], item["tarefa_id"]): item
        for item in evidencias
    }
    evi_refs = set(evidencias_por_ref)
    pares_ligados: set[tuple[str, str, str]] = set()
    favoraveis_atomicos: set[str] = set()
    for ligacao in ligacoes:
        alternativa_id = ligacao.get("alternativa_id_logico")
        evidencia_ref = (
            ligacao.get("evidencia_id_logico"),
            ligacao.get("evidencia_tarefa_id"),
        )
        if alternativa_id not in alt_ids or evidencia_ref not in evi_refs:
            raise ValueError("ligacao_evidencia_invalida")
        par = (alternativa_id, *evidencia_ref)
        if par in pares_ligados:
            raise ValueError("papel_evidencia_duplicado")
        pares_ligados.add(par)
        snapshot = alternativas_por_id[alternativa_id].get("campos_snapshot") or {}
        fatos = evidencias_por_ref[evidencia_ref].get("fatos_normalizados") or {}
        campos_suportados = ligacao.get("campos_suportados")
        campos_contestados = ligacao.get("campos_contestados")
        if (
            not isinstance(campos_suportados, list)
            or not isinstance(campos_contestados, list)
            or len(campos_suportados) != len(set(campos_suportados))
            or len(campos_contestados) != len(set(campos_contestados))
            or not (set(campos_suportados) | set(campos_contestados)) <= set(snapshot)
        ):
            raise ValueError("escopo_ligacao_evidencia_invalido")
        if ligacao.get("papel") == "favoravel":
            if campos_contestados or not campos_suportados or not all(
                campo in fatos
                and _hash(fatos[campo]) == _hash(snapshot[campo])
                for campo in campos_suportados
            ):
                raise ValueError("evidencia_favoravel_nao_suporta_campos")
            if set(campos_suportados) == set(snapshot) and all(
                campo in fatos and _hash(fatos[campo]) == _hash(valor)
                for campo, valor in snapshot.items()
            ):
                favoraveis_atomicos.add(alternativa_id)
        elif ligacao.get("papel") == "contraria":
            if campos_suportados or not campos_contestados or not all(
                campo in fatos
                and _hash(fatos[campo]) != _hash(snapshot[campo])
                for campo in campos_contestados
            ):
                raise ValueError("evidencia_contraria_nao_contesta_campos")
        else:
            raise ValueError("papel_evidencia_invalido")
    contrarias = {
        item.get("alternativa_id_logico")
        for item in ligacoes if item.get("papel") == "contraria"
    }
    resultado = plano.get("resultado")
    if alternativas and not alt_ids <= favoraveis_atomicos:
        raise ValueError("alternativa_sem_suporte_atomico")
    if resultado == "alternativa_unica" and (
        len(alternativas) != 1 or not alt_ids <= favoraveis_atomicos
        or bool(ausentes_alternativas)
    ):
        raise ValueError("contrato_resultado_unico_invalido")
    if resultado == "alternativas_multiplas" and (
        len(alternativas) < 2 or len({
            _hash(item.get("campos_snapshot")) for item in alternativas
        }) < 2 or not alt_ids <= favoraveis_atomicos
    ):
        raise ValueError("contrato_resultado_multiplo_invalido")
    if resultado == "divergente" and (
        not alternativas or not contrarias or not alt_ids <= favoraveis_atomicos
    ):
        raise ValueError("contrato_resultado_divergente_invalido")
    if resultado in {"evidencia_insuficiente", "cobertura_incompleta"} and not pendencias:
        raise ValueError("contrato_resultado_incompleto_invalido")


__all__ = [
    "ESTADOS_COBERTURA", "ESTADOS_EXECUCAO", "ESTADOS_RESULTADO",
    "HASH_REGRAS_CONFIANCA", "HASH_SCHEMA_POLITICAS",
    "POLITICAS_CAMPOS_OBRIGATORIOS", "REGRAS_CONFIANCA",
    "TABELAS_ANEXO", "TABELAS_CONTROLE", "TABELAS_OPERACIONAIS",
    "campos_politica_assunto", "chave_estavel", "chaves_investigacao", "classificar_cobertura",
    "confianca_explicavel", "id_deterministico", "normalizar_assunto",
    "normalizar_consulta", "normalizar_origem", "planejar_investigacao",
    "resolver_consulta_tarefa", "resultado_investigacao", "sanitizar_payload",
    "validar_plano",
]

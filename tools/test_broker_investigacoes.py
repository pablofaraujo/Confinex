"""Testes offline do broker isolado — nenhum teste toca rede ou Supabase."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from typing import Any

from tools import broker_investigacoes as broker


class RelatorioCapacidadesTest(unittest.TestCase):
    def test_relatorio_verde_e_deterministico(self) -> None:
        primeiro, ok_primeiro = broker.relatorio_capacidades()
        segundo, ok_segundo = broker.relatorio_capacidades()
        self.assertTrue(ok_primeiro)
        self.assertTrue(ok_segundo)
        self.assertEqual(primeiro, segundo)
        relatorio = json.loads(primeiro)
        self.assertEqual(relatorio["schema_version"], broker.SCHEMA_CAPACIDADES)
        self.assertEqual(relatorio["broker_version"], broker.VERSAO_BROKER)
        self.assertTrue(relatorio["todas_ok"])
        nomes = [item["nome"] for item in relatorio["verificacoes"]]
        self.assertIn("sanitizacao_remove_segredos", nomes)
        self.assertIn("dry_run_recusa_publicacao", nomes)
        self.assertIn("rpc_allowlist_fechada", nomes)

    def test_hash_capacidades_e_o_sha256_do_relatorio(self) -> None:
        texto, _ = broker.relatorio_capacidades()
        esperado = hashlib.sha256(texto.encode("utf-8")).hexdigest()
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            codigo = broker.main(["--hash-capacidades"])
        self.assertEqual(codigo, 0)
        self.assertEqual(saida.getvalue().strip(), esperado)

    def test_relatorio_nao_contem_segredo_nem_ambiente(self) -> None:
        texto, _ = broker.relatorio_capacidades()
        self.assertNotIn(broker._SEGREDO_DE_TESTE.hex(), texto)
        for proibido in ("SUPABASE", "Bearer", "apikey"):
            self.assertNotIn(proibido, texto)


class ClienteBrokerTest(unittest.TestCase):
    def test_rpc_fora_da_allowlist_falha_antes_de_qualquer_io(self) -> None:
        cliente = broker.ClienteBroker("https://exemplo.invalid", "chave")
        with self.assertRaises(ValueError):
            cliente.rpc("drop_database", {})
        with self.assertRaises(ValueError):
            cliente.rpc("decidir_pendencia_investigacao", {})

    def test_configuracao_incompleta_e_recusada(self) -> None:
        with self.assertRaises(ValueError):
            broker.ClienteBroker("", "chave")
        with self.assertRaises(ValueError):
            broker.ClienteBroker("https://exemplo.invalid", "")


class TratarPedidoTest(unittest.TestCase):
    def rpc_registrando(self, registro: list[tuple[str, dict[str, Any]]]):
        def rpc(nome: str, payload: Any) -> Any:
            registro.append((nome, dict(payload)))
            return {"eco": nome}
        return rpc

    def test_identidade_do_servidor_prevalece_sobre_o_cliente(self) -> None:
        registro: list[tuple[str, dict[str, Any]]] = []
        resposta = broker.tratar_pedido(
            {"op": "assumir", "p_adaptador": "wey", "p_executor": "intruso"},
            adaptador="outro", executor="broker-teste", dry_run=False,
            rpc=self.rpc_registrando(registro),
        )
        self.assertTrue(resposta["ok"])
        nome, payload = registro[0]
        self.assertEqual(nome, "assumir_tarefa_investigacao")
        self.assertEqual(payload["p_adaptador"], "outro")
        self.assertEqual(payload["p_executor"], "broker-teste")

    def test_op_desconhecida_recusada_sem_rpc(self) -> None:
        resposta = broker.tratar_pedido(
            {"op": "materializar"},
            adaptador="outro", executor="x", dry_run=False,
            rpc=lambda nome, payload: self.fail("rpc não deveria ser chamada"),
        )
        self.assertIn("op_desconhecida", resposta["erro"])

    def test_dry_run_recusa_publicar_mas_permite_adiar(self) -> None:
        registro: list[tuple[str, dict[str, Any]]] = []
        recusa = broker.tratar_pedido(
            {"op": "publicar", "p_tarefa_id": "x"},
            adaptador="outro", executor="x", dry_run=True,
            rpc=lambda nome, payload: self.fail("publicação atravessou o dry-run"),
        )
        self.assertEqual(recusa["erro"], "dry_run_nao_publica")
        adiada = broker.tratar_pedido(
            {"op": "adiar", "p_tarefa_id": "t", "p_lease_token": "l",
             "p_fencing_token": 1},
            adaptador="outro", executor="x", dry_run=True,
            rpc=self.rpc_registrando(registro),
        )
        self.assertTrue(adiada["ok"])
        self.assertEqual(registro[0][0], "adiar_tarefa_investigacao")

    def test_publicar_exige_atestado_com_hmac(self) -> None:
        resposta = broker.tratar_pedido(
            {"op": "publicar", "p_tarefa_id": "t"},
            adaptador="outro", executor="x", dry_run=False,
            rpc=lambda nome, payload: self.fail("rpc sem atestado"),
        )
        self.assertEqual(resposta["erro"], "atestado_cobertura_obrigatorio")

    def test_lease_e_saturado_no_intervalo_do_banco(self) -> None:
        registro: list[tuple[str, dict[str, Any]]] = []
        broker.tratar_pedido(
            {"op": "assumir", "lease_segundos": 99999},
            adaptador="outro", executor="x", dry_run=False,
            rpc=self.rpc_registrando(registro),
        )
        self.assertEqual(registro[0][1]["p_lease_segundos"], 900)


class ServidorSocketTest(unittest.TestCase):
    def _pedir(self, caminho: str, pedido: dict[str, Any]) -> dict[str, Any]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conexao:
            conexao.connect(caminho)
            conexao.sendall((json.dumps(pedido) + "\n").encode("utf-8"))
            return json.loads(conexao.makefile("rb").readline().decode("utf-8"))

    def test_servidor_atende_e_limpa_o_socket(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "broker.sock")
            respostas: dict[str, Any] = {}

            def rpc(nome: str, payload: Any) -> Any:
                return {"rpc": nome}

            servidor = threading.Thread(
                target=broker.servir, args=(caminho,),
                kwargs=dict(adaptador="outro", executor="teste",
                            dry_run=True, rpc=rpc, limite_pedidos=2),
            )
            servidor.start()
            try:
                for tentativa in range(200):
                    if os.path.exists(caminho):
                        break
                    threading.Event().wait(0.01)
                modo = os.stat(caminho).st_mode & 0o777
                self.assertEqual(modo, 0o600)
                respostas["sonda"] = self._pedir(caminho, {"op": "sonda"})
                respostas["publicar"] = self._pedir(caminho, {"op": "publicar"})
            finally:
                servidor.join(timeout=10)
            self.assertTrue(respostas["sonda"]["ok"])
            self.assertEqual(respostas["publicar"]["erro"], "dry_run_nao_publica")
            self.assertFalse(os.path.exists(caminho))


class EmitirCredencialTest(unittest.TestCase):
    class ClienteFalso:
        def __init__(self) -> None:
            self.inseridos: list[dict[str, Any]] = []

        def inserir_credencial(self, payload: dict[str, Any]) -> None:
            self.inseridos.append(dict(payload))

    def test_emite_sem_vazar_segredo_e_sem_sobrescrever(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            cliente = self.ClienteFalso()
            destino = broker.emitir_credencial(
                cliente, adaptador="outro", adaptador_version="v1",
                chave_id="key_teste", emite_minutos=30, aceita_minutos=60,
                diretorio_saida=pasta,
            )
            self.assertEqual(len(cliente.inseridos), 1)
            registro = cliente.inseridos[0]
            segredo_hex = registro["chave_hmac"].removeprefix("\\x")
            self.assertEqual(len(bytes.fromhex(segredo_hex)), 32)
            with open(destino, encoding="utf-8") as arquivo:
                self.assertEqual(arquivo.read().strip(), segredo_hex)
            self.assertEqual(os.stat(destino).st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                broker.emitir_credencial(
                    cliente, adaptador="outro", adaptador_version="v1",
                    chave_id="key_teste", emite_minutos=30, aceita_minutos=60,
                    diretorio_saida=pasta,
                )

    def test_recusa_adaptador_desconhecido_e_janela_invalida(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            cliente = self.ClienteFalso()
            with self.assertRaises(ValueError):
                broker.emitir_credencial(
                    cliente, adaptador="sintese", adaptador_version="v1",
                    chave_id="key_x", emite_minutos=30, aceita_minutos=60,
                    diretorio_saida=pasta,
                )
            with self.assertRaises(ValueError):
                broker.emitir_credencial(
                    cliente, adaptador="outro", adaptador_version="v1",
                    chave_id="key_y", emite_minutos=60, aceita_minutos=30,
                    diretorio_saida=pasta,
                )
            self.assertEqual(cliente.inseridos, [])

    def test_saida_da_cli_nao_contem_o_segredo(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            cliente = self.ClienteFalso()
            original = broker.cliente_do_ambiente
            broker.cliente_do_ambiente = lambda: cliente  # type: ignore[assignment]
            try:
                saida = io.StringIO()
                with contextlib.redirect_stdout(saida):
                    codigo = broker.main([
                        "--emitir-credencial", "outro",
                        "--chave-id", "key_cli",
                        "--saida", pasta,
                    ])
            finally:
                broker.cliente_do_ambiente = original  # type: ignore[assignment]
            self.assertEqual(codigo, 0)
            segredo_hex = cliente.inseridos[0]["chave_hmac"].removeprefix("\\x")
            self.assertNotIn(segredo_hex, saida.getvalue())
            self.assertIn("key_cli", saida.getvalue())


class VersaoTest(unittest.TestCase):
    def test_versao_imprime_identidade(self) -> None:
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            codigo = broker.main(["--versao"])
        self.assertEqual(codigo, 0)
        self.assertEqual(saida.getvalue().strip(), broker.VERSAO_BROKER)


if __name__ == "__main__":
    unittest.main()

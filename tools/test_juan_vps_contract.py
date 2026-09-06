import unittest
from unittest.mock import patch

import test_juan_vps

from test_juan_vps import auditar_chamadas_midia, validar_contrato_roteador


def chamada(nome, argumentos):
    return {"name": nome, "arguments": argumentos}


class JuanVpsTrajectoryContractTest(unittest.TestCase):
    def test_agente_legado_bloqueia_antes_de_modelo_sessao_ou_limpeza(self):
        with patch.object(test_juan_vps, 'run') as run, \
             patch.object(test_juan_vps, 'limpar_sessao') as limpar:
            with self.assertRaisesRegex(RuntimeError, 'não estão isoladas'):
                test_juan_vps.validar_agente('ficticio.pdf', 'conferir', 'grupo-ficticio')
            run.assert_not_called()
            limpar.assert_not_called()

    def test_executor_remoto_bloqueia_antes_de_ocr_e_snapshot(self):
        with patch.object(test_juan_vps, 'CONFIG', {'testar_agente': True}), \
             patch.object(test_juan_vps, 'snapshot') as snapshot, \
             patch.object(test_juan_vps, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'não estão isoladas'):
                test_juan_vps.main()
            snapshot.assert_not_called()
            run.assert_not_called()

    def test_contrato_extrato_pdf_e_vinculo_bidirecional(self):
        source = "\n".join(
            (
                "def parse_pdf_bank_statement(): pass",
                'x = {"classe": "extrato_bancario", "importado": False}',
                'x = {"resultado": {"operation_draft_id": draft["id"]}}',
                'x = {"duplicado": True}',
                'raise Error("a mesma origem já existe com classificação diferente")',
            )
        )
        validar_contrato_roteador(source)

    def test_contrato_rejeita_roteador_sem_extrato_bancario(self):
        with self.assertRaisesRegex(RuntimeError, "parse_pdf_bank_statement"):
            validar_contrato_roteador("def route(): pass")

    def test_duas_tentativas_usam_roteador(self):
        referencia = "media://inbound/anexo.pdf"
        calls = [
            chamada(
                "bash",
                {
                    "command": (
                        "python3 arquivo_grupo_router.py "
                        f"{referencia} --dry-run"
                    )
                },
            ),
            chamada(
                "bash",
                {
                    "command": (
                        "python3 arquivo_grupo_router.py "
                        f"{referencia} --dry-run"
                    )
                },
            ),
        ]
        auditar_chamadas_midia(calls, referencia, minimo_roteador=2)

    def test_rejeita_pdf_depois_do_roteador(self):
        referencia = "media://inbound/anexo.pdf"
        calls = [
            chamada(
                "bash",
                {
                    "command": (
                        "python3 arquivo_grupo_router.py "
                        f"{referencia} --dry-run"
                    )
                },
            ),
            chamada("pdf", {"pdf": referencia}),
        ]
        with self.assertRaisesRegex(RuntimeError, "ferramenta interna"):
            auditar_chamadas_midia(calls, referencia)

    def test_rejeita_file_fetch_antes_do_roteador(self):
        referencia = "media://inbound/anexo.pdf"
        calls = [
            chamada("file_fetch", {"path": referencia}),
            chamada(
                "bash",
                {
                    "command": (
                        "python3 arquivo_grupo_router.py "
                        f"{referencia} --dry-run"
                    )
                },
            ),
        ]
        with self.assertRaisesRegex(RuntimeError, "ferramenta interna"):
            auditar_chamadas_midia(calls, referencia)

    def test_rejeita_roteador_sem_dry_run(self):
        referencia = "media://inbound/anexo.pdf"
        calls = [
            chamada(
                "bash",
                {"command": f"python3 arquivo_grupo_router.py {referencia}"},
            )
        ]
        with self.assertRaisesRegex(RuntimeError, "fora de dry-run"):
            auditar_chamadas_midia(calls, referencia)


if __name__ == "__main__":
    unittest.main()

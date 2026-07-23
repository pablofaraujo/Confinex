import unittest

from test_juan_vps import auditar_chamadas_midia


def chamada(nome, argumentos):
    return {"name": nome, "arguments": argumentos}


class JuanVpsTrajectoryContractTest(unittest.TestCase):
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

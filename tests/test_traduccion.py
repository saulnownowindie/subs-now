import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

import traduccion_google as tg


class TraduccionContextoTests(unittest.TestCase):
    def test_clave_normaliza(self):
        self.assertEqual(tg.clave("  Hola,   mundo! "), "hola, mundo")
        self.assertEqual(tg.clave("hola"), tg.clave("Hola."))
        self.assertNotEqual(tg.clave("hola"), tg.clave("adios"))

    def test_terminos_se_protegen_y_se_restauran(self):
        terminos = tg.normalizar_terminos(["Sonny"], "", "AC/DC, Sony Music, BrandX")
        self.assertIn("Sony Music", terminos)
        self.assertIn("BrandX", terminos)
        self.assertIn("AC/DC", terminos)
        self.assertEqual(len(terminos), len({t.casefold() for t in terminos}))  # sin duplicados
        texto, mapa = tg.enmascarar("Hello Sony Music and Sonny today", terminos)
        self.assertIn("T0", texto)
        self.assertNotIn("Sony", texto)
        token_sonny = next((t for t, orig in mapa.items() if orig == "Sonny"), None)
        self.assertIsNotNone(token_sonny)
        self.assertIn(token_sonny, texto)
        self.assertEqual(tg.desenmascarar(texto, terminos), "Hello Sony Music and Sonny today")

    def test_ventana_respetan_limites(self):
        lineas = [f"frase numero {i} con un poco de texto para medir" for i in range(60)]
        grupos = tg.ventanas(lineas)
        for g in grupos:
            self.assertLessEqual(len(g), tg.MAX_VENTANA_LINEAS)
            self.assertLessEqual(sum(len(x) + 1 for x in g), tg.MAX_VENTANA_CARACTERES)

    def test_traductor_reusa_memoria_y_deduplica(self):
        with tempfile.TemporaryDirectory() as tmp:
            traductor = tg.TraductorGoogle(cache_path=str(pathlib.Path(tmp) / "cache.json"))
            llamadas = []

            def falsa_lote(lineas, origen, destino):
                llamadas.append(list(lineas))
                return [f"TRAD-{i}" for i in range(len(lineas))]

            traductor._lote = lambda lineas, origen, destino: falsa_lote(lineas, origen, destino)
            terminos = tg.normalizar_terminos(["Sonny"])
            out = traductor.traducir(
                ["Hola Sonny que tal", "Hola Sonny que tal", "Seguimos hablando ahora"],
                "en", "es", terminos=terminos)
            self.assertEqual(out, ["TRAD-0", "TRAD-0", "TRAD-1"])
            self.assertEqual(len(llamadas), 1)
            self.assertEqual(llamadas[0][0], "Hola T0 que tal")


if __name__ == "__main__":
    unittest.main()

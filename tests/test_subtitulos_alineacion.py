import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP = ROOT.parent / "app"
SOURCE = (APP / "subtitulos.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def load_function(name):
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            ns = {"math": __import__("math")}
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP / "subtitulos.py"), "exec"), ns)
            return ns[name]
    raise AssertionError(f"No se encontró {name}")


class AlineacionTests(unittest.TestCase):
    def test_factor_tempo_seguro_para_factor_normal_y_extremo(self):
        factor = load_function("_factor_tempo_seguro")
        self.assertEqual(factor(1.2), "atempo=0.833333333")
        self.assertIn("atempo=0.5", factor(3.0))

    def test_armar_bloques_sanea_timestamps_invertidos(self):
        # Extrae también las dependencias puras necesarias de forma controlada.
        ns = {"_PUNT_FINAL": ",.;:!?…", "_COMILLAS_PARES": {
            "“": "”", "‘": "’", '"': '"', "'": "'", "«": "»", "‹": "›"
        }}
        for name in ("limpiar_puntuacion_final", "_envolver_comillas"):
            for node in TREE.body:
                if isinstance(node, ast.FunctionDef) and node.name == name:
                    exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP / "subtitulos.py"), "exec"), ns)
        fn_node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "armar_bloques")
        exec(compile(ast.Module(body=[fn_node], type_ignores=[]), str(APP / "subtitulos.py"), "exec"), ns)
        result = ns["armar_bloques"]([["uno", 2.0, 2.4, None], ["dos", 2.2, 2.8, None]])
        self.assertEqual(len(result), 2)
        self.assertGreaterEqual(result[1][1], result[0][2])

    def _retime_functions(self):
        ns = {"math": __import__("math")}
        required = ("_factor_velocidad_item", "_mapear_intervalo_audio", "_filtro_tempo_retime")
        for name in required:
            node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP / "subtitulos.py"), "exec"), ns)
        return ns

    def test_retime_110_mapea_audio_fuente_a_tiempo_de_timeline(self):
        class Item:
            def GetStart(self): return 100
            def GetEnd(self): return 400
            def GetSpeed(self): return {"Percentage": 110.0, "PitchCorrection": True}
            def GetSourceStartTime(self): return 20.0
            def GetSourceEndTime(self): return 31.0

        ns = self._retime_functions()
        seek, source_dur, timeline_dur, speed, reverse = ns["_mapear_intervalo_audio"](Item(), 160, 220, 30.0)
        self.assertAlmostEqual(speed, 1.1)
        self.assertAlmostEqual(seek, 22.2)
        self.assertAlmostEqual(source_dur, 2.2)
        self.assertAlmostEqual(timeline_dur, 2.0)
        self.assertFalse(reverse)
        self.assertEqual(ns["_filtro_tempo_retime"](speed), "atempo=1.100000000")

    def test_retime_1x_no_cambia_el_mapa(self):
        class Item:
            def GetStart(self): return 30
            def GetEnd(self): return 150
            def GetSpeed(self): return {"Percentage": 100.0}
            def GetSourceStartTime(self): return 7.0
            def GetSourceEndTime(self): return 11.0

        ns = self._retime_functions()
        seek, source_dur, timeline_dur, speed, reverse = ns["_mapear_intervalo_audio"](Item(), 60, 90, 30.0)
        self.assertEqual(speed, 1.0)
        self.assertEqual((seek, source_dur, timeline_dur), (8.0, 1.0, 1.0))
        self.assertFalse(reverse)
        self.assertEqual(ns["_filtro_tempo_retime"](speed), "")

    def test_retime_inverso_usa_intervalo_fuente_y_reversa(self):
        class Item:
            def GetStart(self): return 100
            def GetEnd(self): return 400
            def GetSpeed(self): return {"Percentage": 110.0}
            def GetSourceStartTime(self): return 31.0
            def GetSourceEndTime(self): return 20.0

        ns = self._retime_functions()
        seek, source_dur, timeline_dur, speed, reverse = ns["_mapear_intervalo_audio"](Item(), 160, 220, 30.0)
        self.assertTrue(reverse)
        self.assertAlmostEqual(seek, 26.6)
        self.assertAlmostEqual(source_dur, 2.2)
        self.assertEqual(ns["_filtro_tempo_retime"](speed, reverse), "areverse,atempo=1.100000000")


if __name__ == "__main__":
    unittest.main()

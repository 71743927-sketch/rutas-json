"""Tests de tools/sincronizar_tarifas.py (solo biblioteca estándar).

    python -m unittest discover -s tools/tests -v

Nunca se conecta a Shalom: las respuestas se simulan.
"""

import importlib.util
import io
import json
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / 'sincronizar_tarifas.py'
_spec = importlib.util.spec_from_file_location('sincronizar_tarifas', _SCRIPT)
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

TOKEN = 'tok-SECRETO-9f8e7d6c5b4a'

TARIFA_SHALOM = {
    'peso': 1.4,
    'volumen': 320,
    'sobre': 8,
    'cajapaquetexxs': 8,
    'cajapaquetexs': 10,
    'cajapaquetes': 12,
    'cajapaquetem': 18,
    'cajapaquetel': 24,
    'conexion_aerea': 0,
    'caja_aerea': 0,
}


def _ruta(destino, tipo='LEJANO'):
    return {
        'id': f'181_{destino}',
        'origin': '181',
        'destiny': destino,
        'minPrice': 5,
        'ratePerM3': 270,
        'tariff': {'sobre': 25, 'xxs': 25, 'xs': 25, 's': 25, 'm': 25, 'l': 25},
        'type': tipo,
        'createdAt': '2026-01-29T21:42:08.012Z',
        'updatedAt': '2026-01-29T21:42:08.012Z',
    }


class _Respuesta:
    def __init__(self, cuerpo):
        self._datos = json.dumps(cuerpo).encode('utf-8')

    def read(self):
        return self._datos

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Shalom:
    """Simula Shalom Pro: una lista de respuestas, una por consulta."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.pedidos = []

    def __call__(self, pedido, timeout=None):
        self.pedidos.append(pedido)
        r = self.respuestas.pop(0) if self.respuestas else 'ok'
        if isinstance(r, BaseException):
            raise r
        if isinstance(r, int):
            raise urllib.error.HTTPError(
                st.URL, r, f'error con {TOKEN}', {'Authorization': TOKEN}, None
            )
        if r == 'ok':
            return _Respuesta({'success': True, 'data': {'tariff': TARIFA_SHALOM}})
        return _Respuesta(r)


class SincronizarTarifasTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / 'rutas').mkdir()
        self.archivo = self.dir / 'rutas' / 'origin_181.json'
        self.rutas = [_ruta('315'), _ruta('2'), _ruta('7', 'MEDIO')]
        self.archivo.write_text(
            json.dumps(self.rutas, separators=(',', ':')), encoding='utf-8'
        )
        self.original = self.archivo.read_bytes()
        self.esperas = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _correr(self, shalom, *extra, token=TOKEN):
        salida = io.StringIO()
        codigo = st.main(
            ['--repo', str(self.dir), *extra],
            entorno={'SHALOM_TOKEN': token} if token is not None else {},
            abrir=shalom,
            dormir=self.esperas.append,
            salida=salida,
            ahora=lambda: '2026-11-25T18:00:00.000Z',
        )
        return codigo, salida.getvalue()

    def _todo_lo_escrito(self):
        textos = []
        for f in self.dir.rglob('*'):
            if f.is_file():
                textos.append(f.read_text(encoding='utf-8', errors='replace'))
        return '\n'.join(textos)

    # --- El token nunca se registra ---------------------------------------

    def test_no_registra_el_token_cuando_todo_va_bien(self):
        codigo, log = self._correr(_Shalom(['ok', 'ok', 'ok']))
        self.assertEqual(codigo, 0)
        self.assertNotIn(TOKEN, log)
        self.assertNotIn(TOKEN, self._todo_lo_escrito())

    def test_no_registra_el_token_en_los_errores(self):
        errores = [
            urllib.error.URLError(f'fallo de red {TOKEN}'),
            500,  # HTTPError cuyo mensaje y cabeceras traen el token
            RuntimeError(f'algo raro {TOKEN}'),
        ]
        for error in errores:
            with self.subTest(error=type(error).__name__):
                codigo, log = self._correr(_Shalom([error]))
                self.assertNotIn(TOKEN, log)
                self.assertNotIn(TOKEN, self._todo_lo_escrito())
                self.assertNotEqual(codigo, 1)

    def test_no_registra_el_token_al_detenerse(self):
        for codigo_http in (401, 403, 429):
            with self.subTest(codigo=codigo_http):
                _, log = self._correr(_Shalom([codigo_http]))
                self.assertNotIn(TOKEN, log)
                self.assertNotIn(TOKEN, self._todo_lo_escrito())

    def test_el_registro_oculta_el_token_si_aparece(self):
        salida = io.StringIO()
        st.Registro(salida, TOKEN)(f'cabecera Bearer {TOKEN}')
        self.assertEqual(salida.getvalue(), 'cabecera Bearer ***\n')

    def test_sin_token_no_consulta(self):
        shalom = _Shalom(['ok'])
        codigo, log = self._correr(shalom, token=None)
        self.assertEqual(codigo, 1)
        self.assertEqual(shalom.pedidos, [])
        self.assertIn('SHALOM_TOKEN', log)

    # --- Se detiene ante 401, 403 y 429 -----------------------------------

    def test_se_detiene_ante_401_403_y_429(self):
        for codigo_http in (401, 403, 429):
            with self.subTest(codigo=codigo_http):
                progreso = self.dir / 'tools' / '.progreso' / 'origin_181.json'
                progreso.unlink(missing_ok=True)
                self.archivo.write_bytes(self.original)
                shalom = _Shalom(['ok', codigo_http, 'ok'])
                codigo, log = self._correr(shalom)
                self.assertEqual(codigo, 2)
                # Paró en la 2.ª consulta: la 3.ª nunca se hizo.
                self.assertEqual(len(shalom.pedidos), 2)
                self.assertIn(f'HTTP {codigo_http}', log)
                # rutas/ queda como estaba; lo avanzado, en el progreso.
                self.assertEqual(self.archivo.read_bytes(), self.original)
                self.assertFalse((self.dir / 'rutas' / 'meta.json').exists())
                guardado = json.loads(progreso.read_text(encoding='utf-8'))
                self.assertEqual(list(guardado), ['315'])

    def test_sigue_desde_donde_se_detuvo(self):
        self._correr(_Shalom(['ok', 429]))
        shalom = _Shalom(['ok', 'ok'])
        codigo, _ = self._correr(shalom)
        self.assertEqual(codigo, 0)
        destinos = [json.loads(p.data)['destiny'] for p in shalom.pedidos]
        self.assertEqual(destinos, [2, 7])

    # --- Ritmo y consulta -------------------------------------------------

    def test_espera_2_segundos_o_mas_entre_consultas(self):
        self._correr(_Shalom(['ok', 'ok', 'ok']), '--espera', '0.5')
        self.assertEqual(self.esperas, [2.0, 2.0])
        self.esperas.clear()
        self._correr(_Shalom(['ok'] * 3), '--espera', '3')
        self.assertEqual(self.esperas, [3.0, 3.0])

    def test_consulta_solo_desde_el_origen_con_el_cuerpo_de_shalom(self):
        shalom = _Shalom(['ok', 'ok', 'ok'])
        self._correr(shalom)
        for pedido in shalom.pedidos:
            self.assertEqual(pedido.full_url, st.URL)
            self.assertEqual(pedido.get_method(), 'POST')
            cuerpo = json.loads(pedido.data)
            self.assertEqual(cuerpo['origin'], 181)
            self.assertEqual(
                {k: cuerpo[k] for k in ('width', 'height', 'length', 'weight')},
                {'width': '', 'height': '', 'length': '', 'weight': ''},
            )
        self.assertEqual(
            [json.loads(p.data)['destiny'] for p in shalom.pedidos], [315, 2, 7]
        )

    def test_limite_para_probar_con_pocos_destinos(self):
        shalom = _Shalom(['ok'] * 3)
        codigo, log = self._correr(shalom, '--limite', '1')
        self.assertEqual(codigo, 0)
        self.assertEqual(len(shalom.pedidos), 1)
        self.assertEqual(self.archivo.read_bytes(), self.original)
        self.assertIn('Faltan 2 destinos', log)

    # --- Lo que se guarda -------------------------------------------------

    def test_guarda_la_tarifa_real_con_el_mismo_formato_y_meta(self):
        sin_ruta = {'success': False, 'data': {'tariff': None}}
        codigo, _ = self._correr(_Shalom(['ok', 'ok', sin_ruta]))
        self.assertEqual(codigo, 0)
        rutas = json.loads(self.archivo.read_text(encoding='utf-8'))
        self.assertEqual(len(rutas), 3)
        r315 = rutas[0]
        self.assertEqual(set(r315), set(self.rutas[0]))  # mismas claves
        self.assertEqual(
            r315['tariff'],
            {'sobre': 8, 'xxs': 8, 'xs': 10, 's': 12, 'm': 18, 'l': 24,
             'peso': 1.4, 'volumen': 320},
        )
        self.assertEqual(r315['minPrice'], 8)
        self.assertEqual(r315['ratePerM3'], 320)
        self.assertEqual(r315['type'], 'LEJANO')
        self.assertEqual(r315['createdAt'], '2026-01-29T21:42:08.012Z')
        self.assertEqual(r315['updatedAt'], '2026-11-25T18:00:00.000Z')
        # Sin tarifa: queda como estaba y se informa en meta.json.
        self.assertEqual(rutas[2], self.rutas[2])
        meta = json.loads(
            (self.dir / 'rutas' / 'meta.json').read_text(encoding='utf-8')
        )
        self.assertEqual(meta['origen'], '181')
        self.assertEqual(meta['actualizado'], '2026-11-25T18:00:00.000Z')
        self.assertEqual(meta['actualizados'], 2)
        self.assertEqual(meta['sinTarifa'], ['7'])
        # Mismo estilo de archivo: JSON compacto, sin BOM.
        self.assertTrue(self.archivo.read_bytes().startswith(b'[{"id"'))
        self.assertFalse((self.dir / 'tools' / '.progreso' / 'origin_181.json').exists())

    def test_acepta_la_tarifa_en_data_data_tariff(self):
        cuerpo = {'data': {'data': {'tariff': TARIFA_SHALOM}}}
        self.assertEqual(st.extraer_tarifa(cuerpo)['m'], 18)
        self.assertIsNone(st.extraer_tarifa({'data': {'tariff': {}}}))
        self.assertIsNone(st.extraer_tarifa('<html>'))

    def test_respuesta_html_no_es_tarifa_y_se_reintenta(self):
        class _Html(_Respuesta):
            def __init__(self):
                self._datos = b'<html>login</html>'

        llamadas = []

        def abrir(pedido, timeout=None):
            llamadas.append(pedido)
            return _Html()

        codigo, log = self._correr(abrir, '--limite', '1')
        self.assertEqual(codigo, 0)
        self.assertIn('no es JSON', log)
        self.assertEqual(self.archivo.read_bytes(), self.original)


class ImportarTest(unittest.TestCase):
    """--importar: genera rutas/ desde tarifas ya cotizadas, sin token."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / 'rutas').mkdir()
        self.archivo = self.dir / 'rutas' / 'origin_181.json'
        self.rutas = [_ruta('2'), _ruta('315'), _ruta('7', 'MEDIO')]
        self.archivo.write_text(
            json.dumps(self.rutas, separators=(',', ':')), encoding='utf-8'
        )
        self.original = self.archivo.read_bytes()
        self.datos = {
            'origin': 181,
            'originName': 'JUNIN / HUANCAYO / HUANCAYO / HUANCAYO JR. ICA',
            'fuente': 'Shalom Pro tariff/calculate',
            'fecha': '2026-11-25',
            'columnas': list(st.COLUMNAS_IMPORTAR),
            'cajas_cm': {'m': [24, 30, 20, 5], 'l': [42, 30, 23, 10]},
            'rows': [
                [2, 8, 8, 10, 12, 18, 24, 1.4, 320, 0],
                [315, 8, 8, 10, 12, 18, 24, 1.4, 320, 0],
                [900, 8, 8, 10, 12, 13, 16, 0.7, 160, 0],
                [40, 8, 8, 10, 12, 14, 18, 0.9, 210, 0],
            ],
        }

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _importar(self, datos=None, *extra):
        f = self.dir / 'tarifas.json'
        f.write_text(json.dumps(datos or self.datos), encoding='utf-8')
        salida = io.StringIO()
        shalom = _Shalom([])
        codigo = st.main(
            ['--repo', str(self.dir), '--importar', str(f), *extra],
            entorno={},  # sin token
            abrir=shalom,
            dormir=lambda s: None,
            salida=salida,
        )
        self.assertEqual(shalom.pedidos, [])  # nunca consulta a Shalom
        return codigo, salida.getvalue()

    def test_ruta_181_315_con_m_18_y_l_24(self):
        codigo, _ = self._importar()
        self.assertEqual(codigo, 0)
        rutas = {r['destiny']: r for r in json.loads(self.archivo.read_text('utf-8'))}
        r = rutas['315']
        self.assertEqual(r['tariff']['m'], 18)
        self.assertEqual(r['tariff']['l'], 24)
        self.assertEqual(
            r['tariff'],
            {'sobre': 8, 'xxs': 8, 'xs': 10, 's': 12, 'm': 18, 'l': 24,
             'peso': 1.4, 'volumen': 320},
        )
        self.assertEqual(r['minPrice'], 8)
        self.assertEqual(r['ratePerM3'], 320)
        self.assertEqual(r['updatedAt'], '2026-11-25T05:00:00.000Z')
        self.assertEqual(r['createdAt'], '2026-01-29T21:42:08.012Z')
        self.assertEqual(set(r), set(self.rutas[0]))

    def test_agrega_los_destinos_nuevos_y_los_informa(self):
        codigo, log = self._importar()
        self.assertEqual(codigo, 0)
        rutas = json.loads(self.archivo.read_text('utf-8'))
        # El 7 ya no lo lista Shalom: se quita.
        self.assertEqual([r['destiny'] for r in rutas], ['2', '40', '315', '900'])
        nuevo = next(r for r in rutas if r['destiny'] == '900')
        self.assertEqual(nuevo['id'], '181_900')
        self.assertEqual(nuevo['origin'], '181')
        self.assertEqual(nuevo['tariff']['m'], 13)
        self.assertEqual(nuevo['createdAt'], '2026-11-25T05:00:00.000Z')
        self.assertEqual(set(nuevo), set(self.rutas[0]))
        meta = json.loads((self.dir / 'rutas' / 'meta.json').read_text('utf-8'))
        self.assertEqual(meta['nuevos'], ['40', '900'])
        self.assertEqual(meta['quitados'], ['7'])
        self.assertEqual(meta['actualizado'], '2026-11-25')
        self.assertEqual(meta['origen'], '181')
        self.assertEqual(meta['destinos'], 4)
        self.assertIn('Nuevos: 40, 900', log)
        self.assertIn('Quitados: 7', log)

    def test_importar_dos_veces_deja_las_mismas_rutas(self):
        self._importar()
        primera = self.archivo.read_bytes()
        self._importar()
        self.assertEqual(self.archivo.read_bytes(), primera)

    def test_archivo_invalido_no_escribe_nada(self):
        malos = []
        d = json.loads(json.dumps(self.datos)); d['origin'] = 8; malos.append(d)
        d = json.loads(json.dumps(self.datos)); d['columnas'] = ['destiny']; malos.append(d)
        d = json.loads(json.dumps(self.datos)); d['rows'].append([2] + [8] * 9); malos.append(d)
        d = json.loads(json.dumps(self.datos)); d['rows'][0][5] = 'x'; malos.append(d)
        d = json.loads(json.dumps(self.datos)); d['rows'].append([181] + [8] * 9); malos.append(d)
        d = json.loads(json.dumps(self.datos)); d['fecha'] = ''; malos.append(d)
        for i, datos in enumerate(malos):
            with self.subTest(caso=i):
                codigo, log = self._importar(datos)
                self.assertEqual(codigo, 1)
                self.assertIn('No se escribió nada', log)
                self.assertEqual(self.archivo.read_bytes(), self.original)
                self.assertFalse((self.dir / 'rutas' / 'meta.json').exists())


if __name__ == '__main__':
    unittest.main()

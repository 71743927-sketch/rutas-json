#!/usr/bin/env python3
"""MP-121 · Sincroniza las tarifas reales de Shalom Pro para UN origen.

Consulta POST https://pro.shalom.pe/api/v1/envia_ya/tariff/calculate desde la
agencia de origen (por defecto 181, HUANCAYO JR. ICA) hacia cada destino que
ya figura en rutas/origin_<origen>.json, y vuelve a escribir ese archivo con el
mismo formato y la tarifa real, más rutas/meta.json con la fecha y el origen.

Reglas:
- El token de sesión se lee SOLO de la variable de entorno SHALOM_TOKEN. Nunca
  se escribe en el registro, en archivos ni en los mensajes de error.
- Una consulta cada 2 segundos o más (--espera no acepta menos).
- Se detiene ante 401, 403 o 429 (sesión vencida, sin permiso o demasiadas
  consultas) y guarda lo avanzado; la siguiente ejecución sigue desde ahí.
- Solo usa la biblioteca estándar de Python (3.9 o más).

Uso (PowerShell):
    $env:SHALOM_TOKEN = '...'
    python tools\\sincronizar_tarifas.py --limite 3   # prueba con 3 destinos
    python tools\\sincronizar_tarifas.py              # todos los destinos
    Remove-Item Env:SHALOM_TOKEN

Importar tarifas ya cotizadas (sin consultar a Shalom ni usar token):
    python tools\\sincronizar_tarifas.py --importar C:\\ruta\\shalom_tarifas_181.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, TextIO

URL = 'https://pro.shalom.pe/api/v1/envia_ya/tariff/calculate'
ORIGEN_POR_DEFECTO = '181'
ESPERA_MINIMA = 2.0
CODIGOS_DE_PARADA = (401, 403, 429)
MAX_ERRORES_SEGUIDOS = 5

# Clave en rutas-json -> clave que devuelve Shalom Pro.
CLAVES_TARIFA = {
    'sobre': 'sobre',
    'xxs': 'cajapaquetexxs',
    'xs': 'cajapaquetexs',
    's': 'cajapaquetes',
    'm': 'cajapaquetem',
    'l': 'cajapaquetel',
    'peso': 'peso',
    'volumen': 'volumen',
}
CAJAS = ('sobre', 'xxs', 'xs', 's', 'm', 'l')

MOTIVO_PARADA = {
    401: 'la sesión venció o el token no es válido',
    403: 'la cuenta no tiene permiso para esta consulta',
    429: 'Shalom pidió consultar más despacio',
}

# Resultado guardado en el progreso para un destino sin tarifa.
SIN_TARIFA = 'sin_tarifa'


class Detenido(Exception):
    """Respuesta 401, 403 o 429: hay que parar."""

    def __init__(self, codigo: int) -> None:
        super().__init__(f'HTTP {codigo}')
        self.codigo = codigo


class ErrorConsulta(Exception):
    """Fallo de una consulta que no obliga a parar (se reintenta luego)."""


class Registro:
    """Escribe mensajes quitando cualquier aparición del token."""

    def __init__(self, salida: TextIO, token: str) -> None:
        self._salida = salida
        self._token = token

    def ocultar(self, texto: str) -> str:
        if self._token:
            texto = texto.replace(self._token, '***')
        return texto

    def __call__(self, mensaje: str) -> None:
        self._salida.write(self.ocultar(mensaje) + '\n')
        self._salida.flush()


def _numero(valor) -> Optional[float]:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    try:
        return float(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _entero_si_exacto(valor: float):
    return int(valor) if float(valor).is_integer() else valor


def extraer_tarifa(cuerpo) -> Optional[Dict[str, float]]:
    """Saca {sobre, xxs, xs, s, m, l, peso, volumen} de la respuesta.

    Acepta la tarifa en cuerpo.data.tariff, cuerpo.data.data.tariff o
    cuerpo.tariff. Devuelve None si no hay ningún precio de caja.
    """
    candidatos = []
    if isinstance(cuerpo, dict):
        datos = cuerpo.get('data')
        if isinstance(datos, dict):
            candidatos.append(datos.get('tariff'))
            interno = datos.get('data')
            if isinstance(interno, dict):
                candidatos.append(interno.get('tariff'))
        candidatos.append(cuerpo.get('tariff'))
    for t in candidatos:
        if not isinstance(t, dict):
            continue
        tarifa: Dict[str, float] = {}
        for nuestra, de_shalom in CLAVES_TARIFA.items():
            n = _numero(t.get(de_shalom))
            if n is not None:
                tarifa[nuestra] = _entero_si_exacto(n)
        if any(tarifa.get(c, 0) > 0 for c in CAJAS):
            return tarifa
    return None


def consultar(
    origen: str,
    destino: str,
    token: str,
    modo: str = 'bearer',
    abrir: Callable = urllib.request.urlopen,
    tiempo_limite: float = 20,
) -> Optional[Dict[str, float]]:
    """Una consulta a Shalom Pro. Lanza Detenido ante 401/403/429."""
    cuerpo = json.dumps(
        {
            'origin': int(origen),
            'destiny': int(destino),
            'width': '',
            'height': '',
            'length': '',
            'weight': '',
        }
    ).encode('utf-8')
    cabeceras = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': 'sincronizar-tarifas/1.0',
    }
    if modo == 'cookie':
        cabeceras['Cookie'] = token
    else:
        cabeceras['Authorization'] = f'Bearer {token}'
    pedido = urllib.request.Request(URL, data=cuerpo, headers=cabeceras, method='POST')
    try:
        with abrir(pedido, timeout=tiempo_limite) as respuesta:
            texto = respuesta.read().decode('utf-8-sig')
    except urllib.error.HTTPError as e:
        if e.code in CODIGOS_DE_PARADA:
            raise Detenido(e.code) from None
        raise ErrorConsulta(f'HTTP {e.code}') from None
    except urllib.error.URLError:
        raise ErrorConsulta('sin conexión con Shalom Pro') from None
    except TimeoutError:
        raise ErrorConsulta('Shalom Pro tardó demasiado') from None
    try:
        datos = json.loads(texto)
    except ValueError:
        # Shalom a veces responde una página HTML cuando la sesión no vale.
        raise ErrorConsulta('respuesta que no es JSON') from None
    return extraer_tarifa(datos)


def _ahora_iso() -> str:
    t = _dt.datetime.now(_dt.timezone.utc)
    return t.strftime('%Y-%m-%dT%H:%M:%S.') + f'{t.microsecond // 1000:03d}Z'


def _leer_json(ruta: Path):
    with open(ruta, encoding='utf-8-sig') as f:
        return json.load(f)


def _escribir_json(ruta: Path, datos, compacto: bool) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + '.tmp')
    with open(temporal, 'w', encoding='utf-8', newline='\n') as f:
        if compacto:
            json.dump(datos, f, ensure_ascii=False, separators=(',', ':'))
        else:
            json.dump(datos, f, ensure_ascii=False, indent=2)
            f.write('\n')
    os.replace(temporal, ruta)


def ruta_actualizada(anterior: dict, tarifa: Dict[str, float], fecha: str) -> dict:
    """Misma forma que hoy; cambian tariff, minPrice, ratePerM3 y updatedAt."""
    nueva = dict(anterior)
    nueva['tariff'] = {k: tarifa[k] for k in CLAVES_TARIFA if k in tarifa}
    precios = [tarifa[c] for c in CAJAS if tarifa.get(c, 0) > 0]
    if precios:
        nueva['minPrice'] = min(precios)
    if tarifa.get('volumen', 0) > 0:
        nueva['ratePerM3'] = tarifa['volumen']
    nueva['updatedAt'] = fecha
    return nueva


COLUMNAS_IMPORTAR = [
    'destiny', 'sobre', 'xxs', 'xs', 's', 'm', 'l', 'peso', 'volumen',
    'conexion_aerea',
]


class ImportacionInvalida(Exception):
    """El archivo a importar no tiene la forma esperada."""


def _orden_destino(ruta: dict):
    d = str(ruta.get('destiny'))
    return (0, int(d)) if d.isdigit() else (1, d)


def importar(
    archivo_importar: Path,
    repo: Path,
    origen: str,
    log: Callable[[str], None],
) -> int:
    """Genera rutas/origin_<origen>.json y rutas/meta.json desde un archivo de
    tarifas ya cotizadas ({origin, fecha, columnas, rows, cajas_cm}).

    No consulta a Shalom ni necesita token. Valida todo antes de escribir.
    """
    try:
        datos = _leer_json(archivo_importar)
    except (OSError, ValueError) as e:
        log(f'No se pudo leer el archivo a importar ({type(e).__name__}).')
        return 1
    try:
        if not isinstance(datos, dict):
            raise ImportacionInvalida('no es un objeto JSON')
        if str(datos.get('origin')) != origen:
            raise ImportacionInvalida(
                f'el archivo es del origen {datos.get("origin")}, no del {origen}'
            )
        if datos.get('columnas') != COLUMNAS_IMPORTAR:
            raise ImportacionInvalida(f'columnas distintas de {COLUMNAS_IMPORTAR}')
        fecha_dia = str(datos.get('fecha') or '')
        try:
            _dt.date.fromisoformat(fecha_dia)
        except ValueError:
            raise ImportacionInvalida('falta la fecha (AAAA-MM-DD)') from None
        filas = datos.get('rows')
        if not isinstance(filas, list) or not filas:
            raise ImportacionInvalida('no trae filas')
        tarifas: Dict[str, Dict[str, float]] = {}
        for i, fila in enumerate(filas, start=1):
            if not isinstance(fila, list) or len(fila) != len(COLUMNAS_IMPORTAR):
                raise ImportacionInvalida(f'la fila {i} no tiene 10 columnas')
            destino = str(fila[0]).strip()
            if not destino.isdigit():
                raise ImportacionInvalida(f'la fila {i} no tiene destino')
            if destino == origen:
                raise ImportacionInvalida(f'la fila {i} es el mismo origen')
            if destino in tarifas:
                raise ImportacionInvalida(f'el destino {destino} está repetido')
            valores = {}
            for col, valor in zip(COLUMNAS_IMPORTAR[1:9], fila[1:9]):
                n = _numero(valor)
                if n is None or n < 0:
                    raise ImportacionInvalida(
                        f'la fila {i} (destino {destino}) tiene «{col}» inválido'
                    )
                valores[col] = _entero_si_exacto(n)
            if not any(valores[c] > 0 for c in CAJAS):
                raise ImportacionInvalida(f'el destino {destino} no tiene precios')
            tarifas[destino] = valores
    except ImportacionInvalida as e:
        log(f'Archivo no válido: {e}. No se escribió nada.')
        return 1

    archivo = repo / 'rutas' / f'origin_{origen}.json'
    anteriores = _leer_json(archivo) if archivo.exists() else []
    # Medianoche de Lima (UTC-5) del día de la cotización.
    fecha = f'{fecha_dia}T05:00:00.000Z'

    rutas = []
    vistos = set()
    # Destinos que Shalom ya no lista: se quitan (la app dirá «ruta no
    # disponible» en vez de cobrar una tarifa vieja) y quedan en meta.json.
    quitados: List[str] = []
    for r in anteriores:
        d = str(r.get('destiny'))
        vistos.add(d)
        if d in tarifas:
            rutas.append(ruta_actualizada(r, tarifas[d], fecha))
        else:
            quitados.append(d)
    nuevos = sorted((d for d in tarifas if d not in vistos), key=int)
    for d in nuevos:
        base = {
            'id': f'{origen}_{d}',
            'origin': origen,
            'destiny': d,
            'minPrice': 0,
            'ratePerM3': 0,
            'tariff': {},
            # Sin tipo de ruta: Shalom no lo informa y la app no lo usa.
            'type': None,
            'createdAt': fecha,
            'updatedAt': fecha,
        }
        rutas.append(ruta_actualizada(base, tarifas[d], fecha))
    rutas.sort(key=_orden_destino)

    _escribir_json(archivo, rutas, compacto=True)
    _escribir_json(
        repo / 'rutas' / 'meta.json',
        {
            'origen': origen,
            'origenNombre': datos.get('originName') or '',
            'actualizado': fecha_dia,
            'fuente': datos.get('fuente') or 'Shalom Pro · tariff/calculate',
            'destinos': len(rutas),
            'actualizados': len(tarifas),
            'nuevos': nuevos,
            'quitados': quitados,
            'cajas_cm': datos.get('cajas_cm') or {},
            'tariff': 'sobre, xxs, xs, s, m, l: S/ por caja; peso y volumen: '
                      'tal como los devuelve Shalom',
        },
        compacto=False,
    )
    log(f'Importado: {len(tarifas)} destinos con tarifa real del {fecha_dia}; '
        f'{len(nuevos)} nuevos; {len(quitados)} quitados (Shalom ya no los '
        'lista).')
    if nuevos:
        log('Nuevos: ' + ', '.join(nuevos))
    if quitados:
        log('Quitados: ' + ', '.join(quitados))
    return 0


def main(
    argv: Optional[List[str]] = None,
    *,
    entorno=None,
    abrir: Callable = urllib.request.urlopen,
    dormir: Callable[[float], None] = time.sleep,
    salida: Optional[TextIO] = None,
    ahora: Callable[[], str] = _ahora_iso,
) -> int:
    entorno = os.environ if entorno is None else entorno
    salida = sys.stdout if salida is None else salida
    token = (entorno.get('SHALOM_TOKEN') or '').strip()
    log = Registro(salida, token)

    p = argparse.ArgumentParser(description='Tarifas reales de Shalom Pro para un origen.')
    p.add_argument('--origen', default=ORIGEN_POR_DEFECTO)
    p.add_argument('--repo', default=str(Path(__file__).resolve().parent.parent))
    p.add_argument('--espera', type=float, default=ESPERA_MINIMA,
                   help='segundos entre consultas (mínimo 2)')
    p.add_argument('--modo', choices=('bearer', 'cookie'), default='bearer',
                   help='cómo se envía el token: cabecera Authorization o Cookie')
    p.add_argument('--limite', type=int, default=0,
                   help='consultar como mucho N destinos en esta ejecución (prueba)')
    p.add_argument('--importar', metavar='ARCHIVO',
                   help='no consulta a Shalom: genera rutas/ desde un archivo de '
                        'tarifas ya cotizadas (no necesita token)')
    args = p.parse_args(argv)

    if args.importar:
        origen = str(args.origen).strip()
        return importar(Path(args.importar), Path(args.repo), origen, log)

    if not token:
        log('Falta la variable de entorno SHALOM_TOKEN. No se consultó nada.')
        return 1
    origen = str(args.origen).strip()
    if not origen.isdigit():
        log('El origen debe ser el número de agencia de Shalom (por ejemplo 181).')
        return 1
    espera = max(ESPERA_MINIMA, args.espera)

    repo = Path(args.repo)
    archivo = repo / 'rutas' / f'origin_{origen}.json'
    meta = repo / 'rutas' / 'meta.json'
    progreso_archivo = repo / 'tools' / '.progreso' / f'origin_{origen}.json'
    if not archivo.exists():
        log(f'No existe {archivo}. No se consultó nada.')
        return 1

    rutas = _leer_json(archivo)
    destinos = [str(r['destiny']) for r in rutas if str(r.get('destiny')) != origen]
    progreso: Dict[str, object] = (
        _leer_json(progreso_archivo) if progreso_archivo.exists() else {}
    )
    pendientes = [d for d in destinos if d not in progreso]
    log(f'Origen {origen}: {len(destinos)} destinos, {len(pendientes)} pendientes; '
        f'una consulta cada {espera:g} s.')

    consultados = 0
    errores_seguidos = 0
    try:
        for destino in pendientes:
            if args.limite and consultados >= args.limite:
                break
            if consultados > 0:
                dormir(espera)
            consultados += 1
            try:
                tarifa = consultar(origen, destino, token, args.modo, abrir)
            except Detenido as e:
                _escribir_json(progreso_archivo, progreso, compacto=False)
                log(f'DETENIDO en el destino {destino}: HTTP {e.codigo} '
                    f'({MOTIVO_PARADA[e.codigo]}). Se guardó lo avanzado '
                    f'({len(progreso)} de {len(destinos)}); rutas/ no se tocó.')
                return 2
            except ErrorConsulta as e:
                errores_seguidos += 1
                log(f'  {destino}: no se pudo consultar ({e}); se reintentará.')
                if errores_seguidos >= MAX_ERRORES_SEGUIDOS:
                    _escribir_json(progreso_archivo, progreso, compacto=False)
                    log(f'DETENIDO: {errores_seguidos} errores seguidos. Se guardó '
                        'lo avanzado; rutas/ no se tocó.')
                    return 3
                continue
            errores_seguidos = 0
            progreso[destino] = tarifa if tarifa is not None else SIN_TARIFA
            if consultados % 25 == 0:
                _escribir_json(progreso_archivo, progreso, compacto=False)
                log(f'  {len(progreso)} de {len(destinos)} destinos')
    except KeyboardInterrupt:
        _escribir_json(progreso_archivo, progreso, compacto=False)
        log('Interrumpido. Se guardó lo avanzado; rutas/ no se tocó.')
        return 130
    except Exception as e:  # noqa: BLE001 - nunca mostrar el detalle (podría traer el token)
        _escribir_json(progreso_archivo, progreso, compacto=False)
        log(f'Error inesperado ({type(e).__name__}). Se guardó lo avanzado; '
            'rutas/ no se tocó.')
        return 4

    _escribir_json(progreso_archivo, progreso, compacto=False)
    faltan = [d for d in destinos if d not in progreso]
    if faltan:
        log(f'Faltan {len(faltan)} destinos. Vuelve a ejecutar el script para '
            'continuar; rutas/ no se tocó todavía.')
        return 0

    fecha = ahora()
    sin_tarifa: List[str] = []
    nuevas = []
    for r in rutas:
        d = str(r.get('destiny'))
        resultado = progreso.get(d)
        if isinstance(resultado, dict):
            nuevas.append(ruta_actualizada(r, resultado, fecha))
        else:
            if d != origen:
                sin_tarifa.append(d)
            nuevas.append(r)
    _escribir_json(archivo, nuevas, compacto=True)
    _escribir_json(
        meta,
        {
            'origen': origen,
            'actualizado': fecha,
            'fuente': 'Shalom Pro · POST /api/v1/envia_ya/tariff/calculate',
            'destinos': len(destinos),
            'actualizados': len(destinos) - len(sin_tarifa),
            'sinTarifa': sin_tarifa,
            'tariff': 'sobre, xxs, xs, s, m, l: S/ por caja; peso y volumen: '
                      'tal como los devuelve Shalom',
        },
        compacto=False,
    )
    progreso_archivo.unlink(missing_ok=True)
    log(f'Listo: {len(destinos) - len(sin_tarifa)} rutas con tarifa real; '
        f'{len(sin_tarifa)} sin tarifa (quedan como estaban y van en meta.json).')
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    sys.exit(main())

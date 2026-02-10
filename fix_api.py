#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CORRECTOR ROBUSTO DE API - SHALOM PRO
Version simplificada que corrige todos los archivos correctamente
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Fix encoding on Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PARAMETROS_RUTA = {
    'CERCANO': {
        'volumeDivisor': 200,
        'tarifaPeso': 0.85,
        'cajaMinima': 18,
        'precios': {
            'sobre': 15,
            'xxs': 8,
            'xs': 10,
            's': 12,
            'm': 16,
            'l': 18,
        }
    },
    'MEDIO': {
        'volumeDivisor': 198,
        'tarifaPeso': 0.9,
        'cajaMinima': 20,
        'precios': {
            'sobre': 15,
            'xxs': 8,
            'xs': 10,
            's': 12,
            'm': 16,
            'l': 20,
        }
    },
    'LEJANO': {
        'volumeDivisor': 150,
        'tarifaPeso': 1.1,
        'cajaMinima': 28,
        'precios': {
            'sobre': 20,
            'xxs': 12,
            'xs': 16,
            's': 18,
            'm': 24,
            'l': 28,
        }
    }
}

def corregir_ruta(ruta_dict):
    """Corrige una ruta individual"""
    tipo = ruta_dict.get('type', 'MEDIO')
    if tipo not in PARAMETROS_RUTA:
        tipo = 'MEDIO'
    
    params = PARAMETROS_RUTA[tipo]
    
    # Extraer origen y destino del ID (formato: "{origen}_{destino}")
    id_str = ruta_dict.get('id', '0_0')
    try:
        parts = id_str.split('_')
        origen = int(parts[0])
        destino = int(parts[1]) if len(parts) > 1 else 0
    except:
        origen = 0
        destino = 0
    
    ruta_corregida = {
        'id': ruta_dict.get('id'),
        'origen': origen,
        'destino': destino,
        'volumeDivisor': params['volumeDivisor'],
        'tarifaPeso': params['tarifaPeso'],
        'cajaMinima': params['cajaMinima'],
        'precios': params['precios'],
        'descripcion': f"Ruta {origen} a {destino}",
        'tipo': tipo,
        'activa': True,
        'sincronizadoEn': datetime.utcnow().isoformat() + 'Z',
    }
    
    return ruta_corregida

def procesar_archivo(filepath):
    """Procesa un archivo origen"""
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            try:
                datos = json.load(f)
            except:
                return 0
        
        if not isinstance(datos, list):
            datos = [datos]
        
        rutas_corregidas = []
        for ruta in datos:
            ruta_c = corregir_ruta(ruta)
            if ruta_c:
                rutas_corregidas.append(ruta_c)
        
        if rutas_corregidas:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(rutas_corregidas, f, ensure_ascii=False, indent=2)
            return len(rutas_corregidas)
    except:
        pass
    
    return 0

def main():
    print("=" * 70)
    print("[CORRECTOR] SHALOM PRO - VERSION ROBUSTA")
    print("=" * 70)
    print()
    
    rutas_dir = "rutas"
    if not os.path.exists(rutas_dir):
        print(f"ERROR: Directorio '{rutas_dir}' no encontrado")
        return
    
    archivos = 0
    rutas = 0
    
    for archivo in sorted(Path(rutas_dir).glob('origin_*.json')):
        num = procesar_archivo(str(archivo))
        if num > 0:
            archivos += 1
            rutas += num
            print(f"OK {archivo.name:<20} {num:>4} rutas")
    
    print()
    print("=" * 70)
    print(f"RESULTADO: {archivos} archivos, {rutas} rutas corregidas")
    print("=" * 70)

if __name__ == '__main__':
    main()

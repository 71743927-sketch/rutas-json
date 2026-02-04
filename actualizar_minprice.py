#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script para actualizar minPrice en los archivos JSON de rutas
para que el cambio de dimensiones sea visible en los precios.

PROBLEMA: El minPrice era muy alto, ocultando la diferencia de dimensiones.
SOLUCION: Reducir minPrice significativamente para que el cálculo por volumen sea el dominante.

CAMBIOS:
- LEJANO (270 S/m3): minPrice 25 → 5
- CERCANO (182 S/m3): minPrice 18 → 3  
- MEDIO (110 S/m3): minPrice 15 → 2
"""

import json
import os
from pathlib import Path
import sys

# Configurar UTF-8 para salida
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Directorio con los archivos de rutas
RUTAS_DIR = "rutas-json-repo/rutas"

def update_route_file(filepath):
    """Actualiza los minPrice en un archivo de ruta"""
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        
        # Si es lista, iterar cada ruta
        if isinstance(data, list):
            updated = False
            for route in data:
                old_price = route.get('minPrice')
                rate = route.get('ratePerM3', 0)
                
                # Asignar nuevo minPrice según el rate (inversamente correlacionado con distancia)
                if rate == 270:  # LEJANO
                    route['minPrice'] = 5
                    updated = True
                elif rate == 182:  # CERCANO
                    route['minPrice'] = 3
                    updated = True
                elif rate == 110:  # MEDIO
                    route['minPrice'] = 2
                    updated = True
            
            if updated:
                # Escribir actualizado (sin BOM)
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, separators=(',', ':'), ensure_ascii=False)
                return True
        
        return False
    except Exception as e:
        print(f"ERROR en {filepath}: {e}")
        return False

# Contar archivos procesados
if __name__ == "__main__":
    rutas_path = Path(RUTAS_DIR)
    
    if not rutas_path.exists():
        print(f"ERROR: No existe {RUTAS_DIR}")
        exit(1)
    
    json_files = list(rutas_path.glob("origin_*.json"))
    print(f"Encontrados {len(json_files)} archivos de rutas")
    
    updated_count = 0
    for filepath in json_files:
        if update_route_file(filepath):
            updated_count += 1
            if updated_count % 50 == 0:
                print(f"  Procesados {updated_count}...")
    
    print(f"\n[OK] Actualizados {updated_count} archivos de rutas")
    print("\nNUEVOS minPrice:")
    print("  LEJANO (270 S/m3): 25 -> 5")
    print("  CERCANO (182 S/m3): 18 -> 3")
    print("  MEDIO (110 S/m3): 15 -> 2")
    print("\nAhora el calculo de volumen sera VISIBLE en los precios!")

import subprocess
import time
from pathlib import Path
import sys

# Configuración
RANGO_INICIO = 1
RANGO_FIN = 25  # Cambia esto al número de página final que desees (ej. 5 ejecutará 1, 2, 3, 4)
DELAY_SEGUNDOS = 2  # Tiempo de espera entre ejecuciones para evitar bloqueos

urls = [
    "https://stockx.com/brands/puma?page="
    ]

for url_base in urls:
    marca = url_base.split("/brands/")[1].split("?")[0]
    print(f"\n=========================================")
    print(f" INICIANDO PROCESAMIENTO DE MARCA: {marca.upper()}")
    print(f"=========================================")
    for i in range(RANGO_INICIO, RANGO_FIN + 1):
        if url_base == urls[0] and i < 18:
            print(f"saltando paso de {marca} - {i}")
            continue
        print(f"\n--- Iniciando raspado de la página {i} ---")

        # Construimos la URL dinámica
        url_dinamica = f"{url_base}{i}"

        # Definimos el comando como una lista para evitar problemas de escape de caracteres
        comando = [sys.executable, "main.py", "--url", url_dinamica]

        try:
            # Ejecuta el comando y espera a que termine antes de pasar al siguiente
            resultado = subprocess.run(comando, check=True, text=True)
            print(f"Página {i} completada con éxito.")
        except subprocess.CalledProcessError as e:
            print(f"Error al ejecutar la página {i}: {e}")

        # Pausa opcional para no saturar el servidor consecutivamente
        if i < RANGO_FIN:
            print(f"Esperando {DELAY_SEGUNDOS} segundos antes de la siguiente página...")
            time.sleep(DELAY_SEGUNDOS)

print("\n¡Proceso de automatización finalizado!")
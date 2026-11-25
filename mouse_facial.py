import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import pyautogui
import time
import os
import math
import ctypes
import urllib.request

# 1. Optimizaciones de PyAutoGUI
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

def tecla_presionada(codigo_vk):
    return (ctypes.windll.user32.GetAsyncKeyState(codigo_vk) & 0x8000) != 0

VK_F8 = 0x77  # Pausar / Reanudar
VK_F9 = 0x78  # Salir

# 2. Descargar el modelo si no existe
model_path = 'face_landmarker.task'
if not os.path.exists(model_path):
    print("Descargando el modelo face_landmarker.task...")
    url = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
    urllib.request.urlretrieve(url, model_path)

# 3. Configurar MediaPipe Face Landmarker
base_options = python.BaseOptions(model_asset_path=model_path)
options = vision.FaceLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.VIDEO,
    num_faces=1,
    output_face_blendshapes=True
)
detector = vision.FaceLandmarker.create_from_options(options)

# 4. Ajustes de sensibilidad y umbrales
screen_w, screen_h = pyautogui.size()

BOX_MIN_X, BOX_MAX_X = 0.35, 0.65
BOX_MIN_Y, BOX_MAX_Y = 0.43, 0.57

SUAVIZADO = 8
ZONA_MUERTA = 4
prev_x, prev_y = screen_w // 2, screen_h // 2

UMBRAL_SONRISA = 0.40       # Sonrisa corta = Clic Izq | Sostenida = Scroll
TIEMPO_PARA_SCROLL = 0.45   # Segundos para entrar en modo scroll
SENSIBILIDAD_SCROLL = 2500
UMBRAL_BOCA_DER = 0.22      # Abrir poco la boca = Clic Derecho
UMBRAL_CEJAS = 0.45         # Levantar cejas = Agarrar / Soltar (Arrastrar)
COOLDOWN_CLIC = 0.5

# Variables de estado
sonriendo = False
inicio_sonrisa = 0.0
modo_scroll = False
y_base_scroll = 0.0
ultimo_scroll = 0.0

arrastrando = False
cejas_arriba_ant = False

ultimo_clic = 0.0
pausado = False
f8_anterior = False
mensaje_accion = ""
tiempo_mensaje = 0.0
prev_frame_time = time.time()

# --- FUNCIONES DE DISEÑO (HUD ESTÉTICO) ---
def dibujar_esquinas(img, x1, y1, x2, y2, color, grosor=2, largo=18):
    # Esquina superior izquierda
    cv2.line(img, (x1, y1), (x1 + largo, y1), color, grosor)
    cv2.line(img, (x1, y1), (x1, y1 + largo), color, grosor)
    # Esquina superior derecha
    cv2.line(img, (x2, y1), (x2 - largo, y1), color, grosor)
    cv2.line(img, (x2, y1), (x2, y1 + largo), color, grosor)
    # Esquina inferior izquierda
    cv2.line(img, (x1, y2), (x1 + largo, y2), color, grosor)
    cv2.line(img, (x1, y2), (x1, y2 - largo), color, grosor)
    # Esquina inferior derecha
    cv2.line(img, (x2, y2), (x2 - largo, y2), color, grosor)
    cv2.line(img, (x2, y2), (x2, y2 - largo), color, grosor)

def dibujar_barra(img, x, y, ancho, alto, valor, umbral, etiqueta, color_activo):
    valor = max(0.0, min(1.0, valor))
    # Fondo de la barra
    cv2.rectangle(img, (x, y), (x + ancho, y + alto), (50, 50, 55), cv2.FILLED)
    # Relleno proporcional
    color = color_activo if valor >= umbral else (130, 140, 150)
    cv2.rectangle(img, (x, y), (x + int(ancho * valor), y + alto), color, cv2.FILLED)
    # Marca blanca del umbral
    ux = x + int(ancho * umbral)
    cv2.line(img, (ux, y - 2), (ux, y + alto + 2), (255, 255, 255), 2)
    # Etiqueta
    cv2.putText(img, f"{etiqueta} {valor:.2f}", (x, y - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 230, 230), 1, cv2.LINE_AA)

# 5. Ventana compacta y siempre encima
NOMBRE_VENTANA = 'Mouse Facial Pro (F8: Pausa | F9: Salir)'
cv2.namedWindow(NOMBRE_VENTANA, cv2.WINDOW_NORMAL)
cv2.resizeWindow(NOMBRE_VENTANA, 420, 315)
cv2.setWindowProperty(NOMBRE_VENTANA, cv2.WND_PROP_TOPMOST, 1)

cap = cv2.VideoCapture(0)
print("Listo. Sonrisa = Clic/Scroll | Boca = Clic Der | Cejas = Arrastrar/Soltar.")

with detector:
    while cap.isOpened():
        if tecla_presionada(VK_F9):
            break

        f8_actual = tecla_presionada(VK_F8)
        if f8_actual and not f8_anterior:
            pausado = not pausado
            if pausado and arrastrando:
                pyautogui.mouseUp()
                arrastrando = False
        f8_anterior = f8_actual

        success, frame = cap.read()
        if not success:
            break

        frame = cv2.flip(frame, 1)
        h, w, _ = frame.shape

        # Cálculo de FPS
        ahora = time.time()
        fps = int(1 / max(0.001, ahora - prev_frame_time))
        prev_frame_time = ahora

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        timestamp_ms = int(ahora * 1000)

        result = detector.detect_for_video(mp_image, timestamp_ms)

        sonrisa, boca, cejas = 0.0, 0.0, 0.0

        if result.face_landmarks and not pausado:
            face = result.face_landmarks[0]
            nariz = face[1]

            # --- A. LECTURA DE GESTOS ---
            if result.face_blendshapes:
                gestos = {b.category_name: b.score for b in result.face_blendshapes[0]}
                sonrisa = (gestos.get('mouthSmileLeft', 0.0) + gestos.get('mouthSmileRight', 0.0)) / 2.0
                boca = gestos.get('jawOpen', 0.0)
                cejas = gestos.get('browInnerUp', 0.0)

                # 1. Levantar cejas -> Alternar ARRASTRAR (mantener clic izquierdo)
                cejas_arriba = cejas > UMBRAL_CEJAS
                if cejas_arriba and not cejas_arriba_ant and not modo_scroll:
                    if not arrastrando:
                        pyautogui.mouseDown()
                        arrastrando = True
                        mensaje_accion = "ARRASTRANDO..."
                        tiempo_mensaje = ahora
                    else:
                        pyautogui.mouseUp()
                        arrastrando = False
                        mensaje_accion = "SOLTADO"
                        tiempo_mensaje = ahora
                cejas_arriba_ant = cejas_arriba

                # 2. Sonrisa corta = Clic Izq (o soltar arrastre) | Sostenida = Scroll
                if sonrisa > UMBRAL_SONRISA:
                    if arrastrando:
                        pyautogui.mouseUp()
                        arrastrando = False
                        mensaje_accion = "SOLTADO"
                        tiempo_mensaje = ahora
                    elif not sonriendo:
                        sonriendo = True
                        inicio_sonrisa = ahora
                        y_base_scroll = nariz.y
                    else:
                        if ahora - inicio_sonrisa >= TIEMPO_PARA_SCROLL:
                            modo_scroll = True
                            delta_y = nariz.y - y_base_scroll
                            if abs(delta_y) > 0.012 and (ahora - ultimo_scroll > 0.05):
                                paso = int(-delta_y * SENSIBILIDAD_SCROLL)
                                pyautogui.scroll(paso)
                                ultimo_scroll = ahora
                else:
                    if sonriendo:
                        if not modo_scroll and (ahora - ultimo_clic > COOLDOWN_CLIC):
                            pyautogui.click()
                            ultimo_clic = ahora
                            mensaje_accion = "CLIC IZQ"
                            tiempo_mensaje = ahora
                        sonriendo = False
                        modo_scroll = False

                # 3. Boca = Clic Derecho (solo si no estás arrastrando ni sonriendo)
                if not sonriendo and not arrastrando and boca > UMBRAL_BOCA_DER and (ahora - ultimo_clic > COOLDOWN_CLIC):
                    pyautogui.rightClick()
                    ultimo_clic = ahora
                    mensaje_accion = "CLIC DER"
                    tiempo_mensaje = ahora

            # --- B. MOVIMIENTO DEL CURSOR ---
            nx, ny = int(nariz.x * w), int(nariz.y * h)
            if arrastrando:
                color_nariz = (255, 80, 220)   # Magenta al arrastrar
            elif modo_scroll:
                color_nariz = (0, 220, 255)    # Amarillo al hacer scroll
            else:
                color_nariz = (255, 220, 0)    # Cian normal

            cv2.circle(frame, (nx, ny), 8, color_nariz, 2, cv2.LINE_AA)
            cv2.circle(frame, (nx, ny), 3, (255, 255, 255), cv2.FILLED)

            if not modo_scroll:
                norm_x = max(0.0, min(1.0, (nariz.x - BOX_MIN_X) / (BOX_MAX_X - BOX_MIN_X)))
                norm_y = max(0.0, min(1.0, (nariz.y - BOX_MIN_Y) / (BOX_MAX_Y - BOX_MIN_Y)))

                target_x = norm_x * screen_w
                target_y = norm_y * screen_h

                distancia_mov = math.hypot(target_x - prev_x, target_y - prev_y)
                if distancia_mov > ZONA_MUERTA:
                    curr_x = prev_x + (target_x - prev_x) / SUAVIZADO
                    curr_y = prev_y + (target_y - prev_y) / SUAVIZADO
                    pyautogui.moveTo(curr_x, curr_y)
                    prev_x, prev_y = curr_x, curr_y

        # --- C. INTERFAZ HUD SEMISTRANSPARENTE ---
        # Caja de sensibilidad con esquinas limpias
        x1, y1 = int(BOX_MIN_X * w), int(BOX_MIN_Y * h)
        x2, y2 = int(BOX_MAX_X * w), int(BOX_MAX_Y * h)
        color_caja = (255, 80, 220) if arrastrando else ((0, 220, 255) if modo_scroll else (0, 255, 140))
        dibujar_esquinas(frame, x1, y1, x2, y2, color_caja, grosor=2, largo=16)

        # Paneles oscuros semitransparentes (arriba y abajo)
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 42), (18, 18, 22), cv2.FILLED)
        cv2.rectangle(overlay, (0, h - 54), (w, h), (18, 18, 22), cv2.FILLED)
        frame = cv2.addWeighted(overlay, 0.72, frame, 0.28, 0)

        # Estado actual (barra superior)
        if pausado:
            estado_txt, color_est = "PAUSADO (F8)", (80, 80, 255)
        elif arrastrando:
            estado_txt, color_est = "ARRASTRANDO (Cejas: soltar)", (255, 100, 240)
        elif modo_scroll:
            estado_txt, color_est = "MODO SCROLL", (0, 220, 255)
        else:
            estado_txt, color_est = "ACTIVO", (0, 255, 140)

        cv2.circle(frame, (18, 21), 6, color_est, cv2.FILLED)
        cv2.putText(frame, estado_txt, (32, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (245, 245, 245), 1, cv2.LINE_AA)
        cv2.putText(frame, f"{fps} FPS", (w - 82, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 220, 255), 1, cv2.LINE_AA)

        # Aviso flotante de acción
        if ahora - tiempo_mensaje < 0.65:
            cv2.putText(frame, mensaje_accion, (16, 68), cv2.FONT_HERSHEY_SIMPLEX, 0.75, color_caja, 2, cv2.LINE_AA)

        # Barras de métricas en vivo (barra inferior)
        ancho_barra = (w - 60) // 3
        dibujar_barra(frame, 15, h - 22, ancho_barra, 10, sonrisa, UMBRAL_SONRISA, "Sonrisa", (0, 255, 140))
        dibujar_barra(frame, 30 + ancho_barra, h - 22, ancho_barra, 10, boca, UMBRAL_BOCA_DER, "Boca", (0, 200, 255))
        dibujar_barra(frame, 45 + ancho_barra * 2, h - 22, ancho_barra, 10, cejas, UMBRAL_CEJAS, "Cejas", (255, 100, 240))

        cv2.imshow(NOMBRE_VENTANA, frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

# Seguridad: soltar el clic si cierras el programa mientras arrastrabas
if arrastrando:
    pyautogui.mouseUp()

cap.release()
cv2.destroyAllWindows()
"""
OpenCV-based image preprocessing pipeline.
Applied before Document AI submission to improve OCR accuracy.
"""
import cv2
import numpy as np


def preprocess_image(image_bytes: bytes) -> bytes:
    """
    Applies binarisation, deskewing, and noise reduction to a raw image.
    Returns the preprocessed image as JPEG bytes.
    """
    # Decode bytes → OpenCV Mat
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Invalid image bytes: decoding failed.")

    # Step 1: Greyscale conversion
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Step 2: Binarisation (Otsu threshold for adaptive document contrast)
    _, binary = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Step 3: Noise reduction (morphological opening to remove small artifacts)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    # Step 4: Deskewing (correct physical scan rotation up to ±15°)
    coords = np.column_stack(np.where(cleaned > 0))
    if len(coords) > 0:
        rect = cv2.minAreaRect(coords)
        angle = rect[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        if abs(angle) <= 15:   # Only correct small, realistic skews
            h, w = cleaned.shape
            centre = (w // 2, h // 2)
            M = cv2.getRotationMatrix2D(centre, angle, 1.0)
            cleaned = cv2.warpAffine(cleaned, M, (w, h), flags=cv2.INTER_CUBIC,
                                     borderMode=cv2.BORDER_REPLICATE)

    # Encode back to JPEG bytes for Document AI submission
    _, output = cv2.imencode('.jpg', cleaned, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return output.tobytes()

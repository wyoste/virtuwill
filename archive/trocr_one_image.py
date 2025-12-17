from pathlib import Path
import torch
from PIL import Image
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

IMG_PATH = Path("/Users/wyoste/Documents/Journals POC/FD7461A8-9F65-406A-A4E1-188B964847C2_1_102_o.jpeg")

# Handwriting-focused model
MODEL_NAME = "microsoft/trocr-base-handwritten"

device = "mps" if torch.backends.mps.is_available() else "cpu"

processor = TrOCRProcessor.from_pretrained(MODEL_NAME)
model = VisionEncoderDecoderModel.from_pretrained(MODEL_NAME).to(device)

image = Image.open(IMG_PATH).convert("RGB")

pixel_values = processor(images=image, return_tensors="pt").pixel_values.to(device)

# You can tweak generation params later
generated_ids = model.generate(pixel_values, max_new_tokens=256)
text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]

OUT_PATH = IMG_PATH.with_suffix(".trocr.txt")
OUT_PATH.write_text(text, encoding="utf-8")
print(f"Saved OCR output to: {OUT_PATH}")


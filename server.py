from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
import shutil
import uuid
import os
import torch
import torchaudio as ta
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

app = FastAPI()

original_load = torch.load
def patched_load(*args, **kwargs):
    if 'map_location' not in kwargs:
        kwargs['map_location'] = 'cpu'
    return original_load(*args, **kwargs)
torch.load = patched_load

multilingual_model = None
tasks = {}

@app.on_event("startup")
def load_model():
    global multilingual_model
    # mps seems to be used in main.py, fallback to cpu
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Loading Chatterbox model on {device}...")
    try:
        multilingual_model = ChatterboxMultilingualTTS.from_pretrained(device=device)
        print("Model loaded successfully.")
    except Exception as e:
        print(f"Error loading model: {e}")

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

def process_audio(task_id: str, text: str, input_path: str, output_path: str, temperature: float, cfg_weight: float, exaggeration: float):
    try:
        print(f"[{task_id}] Generating audio for text: {text}")
        wav = multilingual_model.generate(
            text, 
            audio_prompt_path=input_path, 
            language_id="tr",
            temperature=temperature,
            cfg_weight=cfg_weight,
            exaggeration=exaggeration
        )
        ta.save(output_path, wav, multilingual_model.sr)
        
        # Optionally, remove input_path here to save space
        if os.path.exists(input_path):
            os.remove(input_path)
            
        tasks[task_id]["status"] = "done"
        print(f"[{task_id}] Generation completed successfully.")
    except Exception as e:
        print(f"[{task_id}] Error generating clone:", str(e))
        tasks[task_id]["status"] = "error"
        tasks[task_id]["error"] = str(e)

@app.post("/api/clone")
def clone_voice(
    background_tasks: BackgroundTasks,
    text: str = Form(...), 
    file: UploadFile = File(...),
    temperature: float = Form(0.8),
    cfg_weight: float = Form(0.5),
    exaggeration: float = Form(0.5)
):
    if multilingual_model is None:
        raise HTTPException(status_code=500, detail="Model initialization failed.")
    
    task_id = str(uuid.uuid4())
    input_path = f"temp_input_{task_id}.wav"
    output_path = f"temp_output_{task_id}.wav"
    
    with open(input_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    tasks[task_id] = {"status": "processing"}
    background_tasks.add_task(process_audio, task_id, text, input_path, output_path, temperature, cfg_weight, exaggeration)
    
    return {"task_id": task_id}

@app.get("/api/status/{task_id}")
def get_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]

@app.get("/api/download/{task_id}")
def download_audio(task_id: str):
    if task_id not in tasks or tasks[task_id]["status"] != "done":
        raise HTTPException(status_code=404, detail="Audio not ready or task not found")
    output_path = f"temp_output_{task_id}.wav"
    return FileResponse(
        output_path, 
        media_type="audio/wav", 
        filename="cloned_voice.wav"
    )

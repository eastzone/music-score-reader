import streamlit as st
import os

# --- 1. FORCE CPU & HIDE GPU (Must be before imports) ---
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["ORT_TENSORRT_ENGINE_CACHE_ENABLE"] = "0"

import shutil
import sys
import importlib.util
import requests
import glob
from pathlib import Path
from pdf2image import convert_from_path
from music21 import converter
from midi2audio import FluidSynth
from pydub import AudioSegment
from PIL import Image, ImageOps

# --- CONFIGURATION ---
SOUNDFONT_URL = "https://raw.githubusercontent.com/musescore/MuseScore/master/share/sound/FluidR3Mono_GM.sf3"
SOUNDFONT_FILE = "FluidR3Mono_GM.sf3"

# --- SYSTEM SETUP ---
def download_file_with_progress(url, dest_path, description):
    if os.path.exists(dest_path): return
    st.write(f"⬇️ Downloading {description}...")
    try:
        response = requests.get(url, stream=True)
        total_size = int(response.headers.get('content-length', 0))
        progress = st.progress(0)
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(1024*1024):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress.progress(min(downloaded / total_size, 1.0))
        progress.empty()
    except Exception as e:
        st.error(f"Failed to download {description}: {e}")
        st.stop()

def patch_oemer_code(base_dir):
    """
    Scans the local oemer copy and removes references to CUDA to prevent ONNX errors.
    """
    for filepath in glob.glob(os.path.join(base_dir, "**/*.py"), recursive=True):
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Patch: Remove CUDAExecutionProvider from the list
        if "CUDAExecutionProvider" in content:
            # Replace the list with just CPU
            # Handles different formatting (quotes, spaces)
            new_content = content.replace("'CUDAExecutionProvider',", "") \
                                 .replace('"CUDAExecutionProvider",', "") \
                                 .replace("'CUDAExecutionProvider'", "") \
                                 .replace('"CUDAExecutionProvider"', "")
            
            if content != new_content:
                print(f"🔧 Patching GPU code in: {filepath}")
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(new_content)

def setup_environment():
    # 1. Download SoundFont
    download_file_with_progress(SOUNDFONT_URL, SOUNDFONT_FILE, "SoundFont")

    # 2. Setup Local Oemer
    local_oemer_dir = "oemer_local"
    target_path = os.path.join(local_oemer_dir, "oemer")
    
    if not os.path.exists(target_path):
        with st.spinner("Setting up AI Engine code..."):
            spec = importlib.util.find_spec("oemer")
            if spec is None:
                st.error("oemer library not found.")
                st.stop()
            
            # Copy system oemer to local
            shutil.copytree(os.path.dirname(spec.origin), target_path, dirs_exist_ok=True)
            
            # RUN THE CODE PATCHER
            patch_oemer_code(target_path)

    # Add to path
    if os.path.abspath(local_oemer_dir) not in sys.path:
        sys.path.insert(0, os.path.abspath(local_oemer_dir))

    # 3. Manual Model Download
    base_ckpt_dir = os.path.join(target_path, "checkpoints")
    models = {
        "unet_big": ("unet_big", "https://github.com/BreezeWhite/oemer/releases/download/checkpoints/1st_model.onnx"),
        "seg_net":  ("seg_net",  "https://github.com/BreezeWhite/oemer/releases/download/checkpoints/2nd_model.onnx")
    }
    
    for key, (folder, url) in models.items():
        folder_path = os.path.join(base_ckpt_dir, folder)
        os.makedirs(folder_path, exist_ok=True)
        file_path = os.path.join(folder_path, "model.onnx")
        if not os.path.exists(file_path):
            download_file_with_progress(url, file_path, f"AI Model: {key}")

# --- CORE PROCESSING ---
class MusicConverter:
    def __init__(self):
        setup_environment()
        self.soundfont = SOUNDFONT_FILE

    def prepare_image(self, file_bytes, file_name, temp_dir):
        output_path = os.path.join(temp_dir, "input_score.png")
        if file_name.lower().endswith(".pdf"):
            temp_pdf = os.path.join(temp_dir, "temp.pdf")
            with open(temp_pdf, "wb") as f: f.write(file_bytes)
            # Use 200 DPI (faster than 300, usually sufficient)
            images = convert_from_path(temp_pdf, first_page=1, last_page=1, dpi=200)
            img = images[0]
        else:
            temp_img = os.path.join(temp_dir, "temp_input")
            with open(temp_img, "wb") as f: f.write(file_bytes)
            img = Image.open(temp_img)

        # FIX: Convert to RGB and STRIP metadata (fixes libpng warning)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Save without ICC profile to clean up the warning
        img.save(output_path, "PNG", icc_profile=None)
        return output_path

    def run_omr(self, image_path):
        import oemer.ete as oemer_ete
        print(f"🎵 Analyzing: {image_path}")
        
        # Mock CLI
        original_argv = sys.argv
        sys.argv = ["oemer", image_path]

        try:
            oemer_ete.main()
        except SystemExit:
            pass
        except Exception as e:
            raise RuntimeError(f"OMR Crash: {e}")
        finally:
            sys.argv = original_argv

        # Check outputs
        possible_files = [
            image_path + ".musicxml",
            os.path.splitext(image_path)[0] + ".musicxml"
        ]
        for f in possible_files:
            if os.path.exists(f): return f
            
        raise FileNotFoundError("AI failed to generate MusicXML. Try a clearer image.")

    def generate_audio(self, xml_path):
        midi_path = xml_path.replace(".musicxml", ".mid")
        wav_path = xml_path.replace(".musicxml", ".wav")
        mp3_path = xml_path.replace(".musicxml", ".mp3")

        try:
            s = converter.parse(xml_path)
            s.write('midi', fp=midi_path)
        except:
            raise ValueError("Could not parse music notation.")

        fs = FluidSynth(self.soundfont)
        fs.midi_to_audio(midi_path, wav_path)
        
        AudioSegment.from_wav(wav_path).export(mp3_path, format="mp3", bitrate="128k")
        return mp3_path

# --- UI ---
st.set_page_config(page_title="AI Sheet Music", page_icon="🎼")
st.title("🎼 AI Sheet Music Player")
st.write("Upload a PDF or Image.")

uploaded_file = st.file_uploader("Upload Score", type=["pdf", "png", "jpg"])

if uploaded_file:
    temp_dir = "temp_workspace"
    if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
    os.makedirs(temp_dir, exist_ok=True)

    st.image(uploaded_file, caption="Preview", width="stretch")

    if st.button("▶️ Generate Audio"):
        status = st.status("Initializing...", expanded=True)
        try:
            converter = MusicConverter()
            
            status.write("🖼️ Reading image...")
            img_path = converter.prepare_image(uploaded_file.getvalue(), uploaded_file.name, temp_dir)
            
            status.write("🎼 Analyzing notes...")
            xml_path = converter.run_omr(img_path)
            
            status.write("🎹 Synthesizing audio...")
            mp3_path = converter.generate_audio(xml_path)
            
            status.update(label="✅ Done!", state="complete", expanded=False)
            st.success("Success!")
            
            with open(mp3_path, "rb") as f:
                st.audio(f.read(), format="audio/mp3")
                
        except Exception as e:
            status.update(label="❌ Failed", state="error")
            st.error(f"Error: {e}")
            print(e)

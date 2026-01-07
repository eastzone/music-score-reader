import streamlit as st
import os
import shutil
import sys
import importlib.util
import requests
from pathlib import Path
from pdf2image import convert_from_path
from music21 import converter
from midi2audio import FluidSynth
from pydub import AudioSegment
from PIL import Image

# --- CONFIGURATION ---
SOUNDFONT_URL = "https://raw.githubusercontent.com/musescore/MuseScore/master/share/sound/FluidR3Mono_GM.sf3"
SOUNDFONT_FILE = "FluidR3Mono_GM.sf3"

# Direct links to oemer models (hosted on GitHub Releases)
# These are the files 'oemer' is trying to download silently
MODEL_URLS = {
    "unet_big": "https://github.com/BreezeWhite/oemer/releases/download/checkpoints/1st_model.onnx",
    "seg_net": "https://github.com/BreezeWhite/oemer/releases/download/checkpoints/2nd_model.onnx"
}

# --- SYSTEM SETUP & PATCHING ---
def download_file_with_progress(url, dest_path, description):
    """Downloads a file with a visible Streamlit progress bar."""
    if os.path.exists(dest_path):
        return

    st.write(f"⬇️ Downloading {description}...")
    try:
        response = requests.get(url, stream=True)
        total_size = int(response.headers.get('content-length', 0))
        
        progress_bar = st.progress(0)
        downloaded = 0
        
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024*1024): # 1MB chunks
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress_bar.progress(min(downloaded / total_size, 1.0))
        
        progress_bar.empty() # Clear bar when done
        
    except Exception as e:
        st.error(f"Failed to download {description}: {e}")
        st.stop()

def setup_environment():
    """
    Sets up SoundFont and OMR Models manually to avoid timeouts.
    """
    # 1. SoundFont
    download_file_with_progress(SOUNDFONT_URL, SOUNDFONT_FILE, "SoundFont")

    # 2. Setup Local Oemer (Permission Fix)
    local_oemer_dir = "oemer_local"
    if not os.path.exists(local_oemer_dir):
        with st.spinner("Setting up AI Engine code..."):
            spec = importlib.util.find_spec("oemer")
            if spec is None:
                st.error("oemer library not found.")
                st.stop()
            
            system_oemer_path = os.path.dirname(spec.origin)
            target_path = os.path.join(local_oemer_dir, "oemer")
            shutil.copytree(system_oemer_path, target_path, dirs_exist_ok=True)
    
    # Add to path if not already there
    if os.path.abspath(local_oemer_dir) not in sys.path:
        sys.path.insert(0, os.path.abspath(local_oemer_dir))

    # 3. Manual Model Download (The Fix for "Stuck" logs)
    # oemer expects models in: oemer_local/oemer/checkpoints/[model_name]/model.onnx
    base_ckpt_dir = os.path.join(local_oemer_dir, "oemer", "checkpoints")
    
    # Map: key -> (folder_name, filename)
    models_to_download = {
        "unet_big": ("unet_big", "model.onnx"),
        "seg_net":  ("seg_net", "model.onnx")
    }

    for key, (folder, filename) in models_to_download.items():
        folder_path = os.path.join(base_ckpt_dir, folder)
        os.makedirs(folder_path, exist_ok=True)
        file_path = os.path.join(folder_path, filename)
        
        # Download only if missing
        if not os.path.exists(file_path):
            download_file_with_progress(MODEL_URLS[key], file_path, f"AI Model: {key}")

# --- CORE PROCESSING ---
class MusicConverter:
    def __init__(self):
        # Run setup immediately
        setup_environment()
        self.soundfont = SOUNDFONT_FILE

    def prepare_image(self, file_bytes, file_name, temp_dir):
        output_path = os.path.join(temp_dir, "input_score.png")
        if file_name.lower().endswith(".pdf"):
            temp_pdf = os.path.join(temp_dir, "temp.pdf")
            with open(temp_pdf, "wb") as f:
                f.write(file_bytes)
            images = convert_from_path(temp_pdf, first_page=1, last_page=1, dpi=300)
            img = images[0]
        else:
            temp_img = os.path.join(temp_dir, "temp_input")
            with open(temp_img, "wb") as f:
                f.write(file_bytes)
            img = Image.open(temp_img)

        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        img.save(output_path, "PNG")
        return output_path

    def run_omr(self, image_path):
        # We must import INSIDE the function to use the local 'oemer' module
        import oemer.ete as oemer_ete
        
        print(f"🎵 Analyzing: {image_path}")
        
        # Mock CLI arguments
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

        # Check for output
        expected = image_path + ".musicxml"
        if os.path.exists(expected): return expected
        
        # Fallback check
        fallback = os.path.splitext(image_path)[0] + ".musicxml"
        if os.path.exists(fallback): return fallback
            
        raise FileNotFoundError("AI failed to generate MusicXML.")

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

# --- UI LOGIC ---
st.set_page_config(page_title="AI Sheet Music Player", page_icon="🎼")
st.title("🎼 AI Sheet Music Player")
st.write("Upload a PDF or Image. I will play it for you.")

uploaded_file = st.file_uploader("Upload Score", type=["pdf", "png", "jpg"])

if uploaded_file:
    temp_dir = "temp_workspace"
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir, exist_ok=True)

    # Use "stretch" to fix the previous error
    st.image(uploaded_file, caption="Preview", width="stretch")

    if st.button("▶️ Generate Audio"):
        # Create container for status updates
        status_box = st.status("Initializing...", expanded=True)
        
        try:
            # 1. Setup & Download
            status_box.write("⚙️ Checking AI models...")
            converter = MusicConverter()
            
            # 2. Process
            status_box.write("🖼️ Reading image...")
            img_path = converter.prepare_image(uploaded_file.getvalue(), uploaded_file.name, temp_dir)
            
            status_box.write("🎼 Analyzing notes (this takes ~30s)...")
            xml_path = converter.run_omr(img_path)
            
            status_box.write("🎹 Synthesizing audio...")
            mp3_path = converter.generate_audio(xml_path)
            
            status_box.update(label="✅ Ready!", state="complete", expanded=False)
            
            st.success("Done!")
            with open(mp3_path, "rb") as f:
                st.audio(f.read(), format="audio/mp3")
                
        except Exception as e:
            status_box.update(label="❌ Failed", state="error")
            st.error(f"Error: {e}")
            print(e) # Logs for debugging

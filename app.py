import streamlit as st
import os
import shutil
import sys
import importlib.util
from pathlib import Path
from pdf2image import convert_from_path
from music21 import converter
from midi2audio import FluidSynth
from pydub import AudioSegment
import requests

# --- CONFIGURATION ---
SOUNDFONT_URL = "https://raw.githubusercontent.com/musescore/MuseScore/master/share/sound/FluidR3Mono_GM.sf3"
SOUNDFONT_FILE = "FluidR3Mono_GM.sf3"

# --- SYSTEM FIXES ---
def download_soundfont():
    """Downloads a smaller soundfont to bypass GitHub 100MB limits."""
    if not os.path.exists(SOUNDFONT_FILE):
        with st.spinner(f"Downloading SoundFont (one-time setup)..."):
            response = requests.get(SOUNDFONT_URL)
            with open(SOUNDFONT_FILE, "wb") as f:
                f.write(response.content)

def setup_oemer():
    """
    Fixes PermissionError on Streamlit Cloud.
    Copies oemer to the local writable folder so it can download its own checkpoints.
    """
    # 1. Check if we already have a local copy
    if os.path.exists("oemer_local"):
        # Add local folder to path so Python imports this one instead of the system one
        if os.path.abspath("oemer_local") not in sys.path:
            sys.path.insert(0, os.path.abspath("oemer_local"))
        return

    with st.spinner("Setting up AI models (first time only)..."):
        # 2. Find where oemer is installed in the system
        spec = importlib.util.find_spec("oemer")
        if spec is None:
            st.error("oemer is not installed in the environment!")
            st.stop()
        
        system_oemer_path = os.path.dirname(spec.origin)
        
        # 3. Copy it to a local folder named 'oemer_local/oemer'
        # We nest it so we can add 'oemer_local' to sys.path and import 'oemer' cleanly
        os.makedirs("oemer_local", exist_ok=True)
        target_path = os.path.join("oemer_local", "oemer")
        
        if not os.path.exists(target_path):
            shutil.copytree(system_oemer_path, target_path)
        
        # 4. Insert into sys.path to ensure we use this writable version
        sys.path.insert(0, os.path.abspath("oemer_local"))

# --- BACKEND LOGIC ---
class MusicOCRConverter:
    def __init__(self):
        download_soundfont()
        setup_oemer() # Run the permission fix
        self.soundfont_path = SOUNDFONT_FILE

    def convert_pdf_to_img(self, pdf_bytes, temp_dir):
        pdf_path = os.path.join(temp_dir, "input.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        images = convert_from_path(pdf_path, first_page=1, last_page=1)
        img_path = os.path.join(temp_dir, "score.png")
        images[0].save(img_path, 'PNG')
        return img_path

    def save_uploaded_image(self, img_bytes, temp_dir):
        img_path = os.path.join(temp_dir, "score.png")
        with open(img_path, "wb") as f:
            f.write(img_bytes)
        return img_path

    def run_omr(self, image_path):
        """Runs oemer using the Python API directly (bypassing CLI issues)."""
        
        # IMPORT OEMER HERE (After setup_oemer ensures it's loading the local writable version)
        import oemer.ete as oemer_ete
        
        # oemer expects the file to be in the working directory for best results
        # We will copy the image to the current root to be safe
        local_img = "temp_omr_input.png"
        shutil.copy(image_path, local_img)
        
        print("Running OMR extraction...")
        # We call the main extraction function directly
        # 'use_tf' is False to ensure we use Onnx (lighter)
        try:
            # We mock the arguments oemer expects
            class Args:
                img_path = local_img
                use_tf = False
                save_path = "./" # Save to current writable dir
            
            # Run extraction
            # This triggers the model download, which will now work because 
            # we are running from the writable 'oemer_local' folder
            oemer_ete.main(Args())
            
            # Identify output file
            # oemer usually outputs: [filename].musicxml
            expected_output = local_img + ".musicxml"
            
            if os.path.exists(expected_output):
                return expected_output
            
            # Fallback check
            base_name = os.path.splitext(local_img)[0]
            if os.path.exists(base_name + ".musicxml"):
                return base_name + ".musicxml"
                
            raise FileNotFoundError("MusicXML file was not generated.")
            
        finally:
            # Cleanup the temp image in root
            if os.path.exists(local_img):
                os.remove(local_img)

    def xml_to_midi(self, xml_path):
        s = converter.parse(xml_path)
        midi_path = xml_path.replace(".musicxml", ".mid")
        s.write('midi', fp=midi_path)
        return midi_path

    def midi_to_mp3(self, midi_path):
        wav_path = midi_path.replace(".mid", ".wav")
        mp3_path = midi_path.replace(".mid", ".mp3")
        fs = FluidSynth(self.soundfont_path)
        fs.midi_to_audio(midi_path, wav_path)
        audio = AudioSegment.from_wav(wav_path)
        audio.export(mp3_path, format="mp3")
        return mp3_path

# --- FRONTEND ---
st.set_page_config(page_title="AI Music Score Player", page_icon="🎵")
st.title("🎵 AI Sheet Music Player")
st.write("Upload a PDF or Image of a music score.")

uploaded_file = st.file_uploader("Choose a music score", type=["pdf", "png", "jpg"])

if uploaded_file is not None:
    temp_dir = "temp_processing"
    os.makedirs(temp_dir, exist_ok=True)
    st.image(uploaded_file, caption="Uploaded Score", use_container_width=True)
    
    if st.button("▶️ Convert to Audio"):
        try:
            converter = MusicOCRConverter()
            with st.spinner("Processing... (This takes 1-2 mins initially)"):
                if uploaded_file.name.endswith(".pdf"):
                    img_path = converter.convert_pdf_to_img(uploaded_file.getvalue(), temp_dir)
                else:
                    img_path = converter.save_uploaded_image(uploaded_file.getvalue(), temp_dir)
                
                # Run the pipeline
                xml_path = converter.run_omr(img_path)
                midi_path = converter.xml_to_midi(xml_path)
                mp3_path = converter.midi_to_mp3(midi_path)
                
            st.success("Conversion Complete!")
            st.subheader("🎧 Listen")
            with open(mp3_path, "rb") as audio_file:
                st.audio(audio_file.read(), format="audio/mp3")
        except Exception as e:
            st.error(f"Error: {e}")
            # Print detailed error to logs for debugging
            import traceback
            traceback.print_exc()
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

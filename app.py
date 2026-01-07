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
# Uses a lightweight SoundFont to save bandwidth
SOUNDFONT_URL = "https://raw.githubusercontent.com/musescore/MuseScore/master/share/sound/FluidR3Mono_GM.sf3"
SOUNDFONT_FILE = "FluidR3Mono_GM.sf3"

# --- SYSTEM SETUP & PATCHING ---
def download_soundfont():
    if not os.path.exists(SOUNDFONT_FILE):
        with st.spinner("Downloading SoundFont (one-time setup)..."):
            try:
                response = requests.get(SOUNDFONT_URL, timeout=30)
                response.raise_for_status()
                with open(SOUNDFONT_FILE, "wb") as f:
                    f.write(response.content)
            except Exception as e:
                st.error(f"Failed to download SoundFont: {e}")
                st.stop()

def setup_oemer_patch():
    """
    CRITICAL FIX: Streamlit Cloud is read-only. 'oemer' tries to download models 
    to its install folder and fails. We copy 'oemer' to a local writable folder 
    and force Python to use that copy.
    """
    local_oemer_dir = "oemer_local"
    
    # Check if already patched in this session
    if os.path.abspath(local_oemer_dir) in sys.path:
        return

    # If the folder exists from a previous run, use it
    if os.path.exists(local_oemer_dir) and os.path.exists(os.path.join(local_oemer_dir, "oemer")):
        sys.path.insert(0, os.path.abspath(local_oemer_dir))
        return

    with st.spinner("Initializing AI Engine (this takes 30s the first time)..."):
        # Find where oemer is installed in the system
        spec = importlib.util.find_spec("oemer")
        if spec is None:
            st.error("❌ Critical: 'oemer' library not found. Check requirements.txt.")
            st.stop()
        
        system_oemer_path = os.path.dirname(spec.origin)
        target_path = os.path.join(local_oemer_dir, "oemer")
        
        # Copy system oemer to local writable directory
        # dirs_exist_ok=True prevents crashes if folder exists partially
        shutil.copytree(system_oemer_path, target_path, dirs_exist_ok=True)
        
        # Insert local path to the TOP of sys.path so imports find this version first
        sys.path.insert(0, os.path.abspath(local_oemer_dir))

# --- CORE PROCESSING ---
class MusicConverter:
    def __init__(self):
        download_soundfont()
        setup_oemer_patch()
        self.soundfont = SOUNDFONT_FILE

    def prepare_image(self, file_bytes, file_name, temp_dir):
        """Converts PDF/Image to a standardized RGB PNG."""
        output_path = os.path.join(temp_dir, "input_score.png")
        
        if file_name.lower().endswith(".pdf"):
            # PDF Processing
            temp_pdf = os.path.join(temp_dir, "temp.pdf")
            with open(temp_pdf, "wb") as f:
                f.write(file_bytes)
            # Convert 1st page only, at 300 DPI for better OCR
            images = convert_from_path(temp_pdf, first_page=1, last_page=1, dpi=300)
            img = images[0]
        else:
            # Image Processing
            temp_img = os.path.join(temp_dir, "temp_input")
            with open(temp_img, "wb") as f:
                f.write(file_bytes)
            img = Image.open(temp_img)

        # CRITICAL: Convert to RGB. oemer fails on 'RGBA' (transparent) images.
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        img.save(output_path, "PNG")
        return output_path

    def run_omr(self, image_path):
        """Runs oemer by mocking command line arguments."""
        # Import inside function to ensure we use the PATCHED version from setup_oemer_patch()
        try:
            import oemer.ete as oemer_ete
        except ImportError:
            # Fallback if patch failed
            import oemer.ete as oemer_ete

        print(f"🎵 Analyzying score: {image_path}")

        # Hack: Modify sys.argv to trick oemer into thinking it was called from CLI
        # This bypasses the need to understand oemer's internal API changes
        original_argv = sys.argv
        sys.argv = ["oemer", image_path]

        try:
            # This will trigger model download (if needed) into our writable folder
            oemer_ete.main()
        except SystemExit as e:
            # oemer calls sys.exit(0) on success, which would kill our server. We catch it.
            if e.code != 0:
                raise RuntimeError(f"OMR Engine exited with error code {e.code}")
        except Exception as e:
            raise RuntimeError(f"OMR Engine crashed: {str(e)}")
        finally:
            sys.argv = original_argv  # Restore system state

        # Check for output. oemer usually appends .musicxml
        expected_output = image_path + ".musicxml"
        if os.path.exists(expected_output):
            return expected_output
        
        # Fallback: sometimes it names it without extension
        no_ext_path = os.path.splitext(image_path)[0] + ".musicxml"
        if os.path.exists(no_ext_path):
            return no_ext_path
            
        raise FileNotFoundError("AI finished but generated no MusicXML file. The image might be too complex or blurry.")

    def generate_audio(self, xml_path):
        """Converts MusicXML -> MIDI -> WAV -> MP3"""
        midi_path = xml_path.replace(".musicxml", ".mid")
        wav_path = xml_path.replace(".musicxml", ".wav")
        mp3_path = xml_path.replace(".musicxml", ".mp3")

        # 1. XML -> MIDI
        try:
            score = converter.parse(xml_path)
            score.write('midi', fp=midi_path)
        except Exception as e:
            raise ValueError(f"Failed to parse music notation: {e}")

        # 2. MIDI -> WAV (FluidSynth)
        if not os.path.exists(self.soundfont):
            raise FileNotFoundError(f"SoundFont file missing: {self.soundfont}")
            
        fs = FluidSynth(self.soundfont)
        fs.midi_to_audio(midi_path, wav_path)

        # 3. WAV -> MP3 (pydub)
        # Using a lower bitrate (128k) to process faster on cloud
        AudioSegment.from_wav(wav_path).export(mp3_path, format="mp3", bitrate="128k")
        
        return mp3_path

# --- UI LOGIC ---
st.set_page_config(page_title="Sheet Music to MP3", page_icon="🎼", layout="centered")

st.title("🎼 Sheet Music to Audio")
st.markdown("Upload a **PDF** or **Image** of sheet music. The AI will read the notes and play them back.")

uploaded_file = st.file_uploader("Upload Score", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file:
    # Use a temp dir that cleans up after itself
    temp_dir = "temp_workspace"
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir, exist_ok=True)

    # Show Preview
    st.image(uploaded_file, caption="Preview", width=None) # width=None respects deprecation warning (auto width)

    if st.button("▶️ Generate Audio"):
        status = st.status("Starting AI Engine...", expanded=True)
        
        try:
            converter = MusicConverter()
            
            status.write("🖼️ Processing Image...")
            img_path = converter.prepare_image(uploaded_file.getvalue(), uploaded_file.name, temp_dir)
            
            status.write("🎼 Reading Notes (OMR)...")
            xml_path = converter.run_omr(img_path)
            
            status.write("🎹 Synthesizing Audio...")
            mp3_path = converter.generate_audio(xml_path)
            
            status.update(label="✅ Done!", state="complete", expanded=False)
            
            # Result
            st.success("Conversion Successful!")
            
            # Audio Player
            with open(mp3_path, "rb") as f:
                audio_bytes = f.read()
                st.audio(audio_bytes, format="audio/mp3")
                st.download_button("Download MP3", audio_bytes, "music.mp3", "audio/mp3")

        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(f"An error occurred: {str(e)}")
            # Debugging info (optional, helps if you see logs)
            print(e)

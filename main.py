from fastapi import FastAPI, UploadFile, File
from fastapi.responses import Response, PlainTextResponse
import json, os, subprocess

# --- Import your modules ---
from pi_model import load_json, json_to_pi_per_function, system_to_pi
from conversion_to_promela import convert_pi_to_promela

app = FastAPI()

# Set a fixed output directory for .pml, pan, and .trail files
OUTPUT_DIR = r"A:\Mobius\api's\fastapi\fastapi\spin_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ------------------------------------------------------------
# 1. Convert high-level agent JSON to π-calculus JSON
# ------------------------------------------------------------
@app.post("/pi_model/")
async def convert_json(file: UploadFile = File(...)):
    file_path = os.path.join(OUTPUT_DIR, file.filename)
    with open(file_path, "wb") as f:
        f.write(await file.read())

    try:
        agents = load_json(file_path)
    except Exception as e:
        return {"error": f"Invalid JSON: {e}"}

    pi_output = json_to_pi_per_function(agents)
    pi_output["System_main"] = system_to_pi(agents)
    result = {"agents": pi_output}

    return result


# ------------------------------------------------------------
# 2. Convert π-calculus JSON into Promela .pml model
# ------------------------------------------------------------
@app.post("/conversion_to_promela/")
async def json_to_promela(file: UploadFile = File(...)):
    """Converts π-calculus JSON into a Promela model (.pml)"""
    file_path = os.path.join(OUTPUT_DIR, file.filename)
    with open(file_path, "wb") as f:
        f.write(await file.read())

    try:
        data = json.load(open(file_path))
    except Exception as e:
        return {"error": f"Invalid JSON: {e}"}

    try:
        promela_text = convert_pi_to_promela(data)
    except Exception as e:
        return {"error": f"Conversion failed: {e}"}

    base_name = os.path.splitext(file.filename)[0]
    output_filename = f"{base_name}_promela_code.pml"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(promela_text)

    return {
        "filename": output_filename,
        "output_directory": OUTPUT_DIR,
        "promela_content": promela_text
    }


from fastapi import FastAPI, UploadFile, File
from fastapi.responses import PlainTextResponse
import os, subprocess



# ------------------------------------------------------------
# Deadlock Checking
# ------------------------------------------------------------
@app.post("/check_deadlock/", response_class=PlainTextResponse)
async def check_deadlock(file: UploadFile = File(...)):
    """Run SPIN to check for deadlocks using:
       spin -a file.pml
       gcc -o pan pan.c
       ./pan
    """
    # Save uploaded file to its own directory
    upload_dir = os.path.dirname(os.path.abspath(file.filename))
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, file.filename)

    with open(file_path, "wb") as f:
        f.write(await file.read())

    # Step 1: spin -a file.pml
    try:
        subprocess.run(["spin", "-a", file_path],
                       cwd=upload_dir, check=True,
                       capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        return f"Spin generation failed:\n{e.stderr or e.stdout}"

    # Step 2: gcc -o pan pan.c
    exe_file = "pan" if os.name == "nt" else "pan"
    try:
        subprocess.run(["gcc", "-o", exe_file, "pan.c"],
                       cwd=upload_dir, check=True,
                       capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        return f"Compilation failed:\n{e.stderr or e.stdout}"

    # Step 3: ./pan
    exe_path = exe_file if os.name == "nt" else f"./{exe_file}"
    try:
        result = subprocess.run([exe_path],
                                cwd=upload_dir, capture_output=True, text=True)
        output_text = result.stdout or result.stderr
    except FileNotFoundError:
        return "Error: pan.exe not found."
    except subprocess.CalledProcessError as e:
        output_text = e.stdout + "\n" + e.stderr

    # Summarize result
    summary = "✅ No deadlocks detected." if "errors: 0" in output_text else "⚠️ Deadlock or errors detected!"
    return f"{summary}\n\nFiles saved in: {upload_dir}\n\n--- SPIN Output ---\n{output_text}"


# ------------------------------------------------------------
# Liveness Checking
# ------------------------------------------------------------
@app.post("/check_liveness/", response_class=PlainTextResponse)
async def check_liveness(file: UploadFile = File(...)):
    """Run SPIN to check for liveness using:
       spin -a file.pml
       gcc -o pan pan.c
       ./pan -a
    """
    upload_dir = os.path.dirname(os.path.abspath(file.filename))
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, file.filename)

    with open(file_path, "wb") as f:
        f.write(await file.read())

    # Step 1: spin -a file.pml
    try:
        subprocess.run(["spin", "-a", file_path],
                       cwd=upload_dir, check=True,
                       capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        return f"Spin generation failed:\n{e.stderr or e.stdout}"

    # Step 2: gcc -o pan pan.c
    exe_file = "pan" if os.name == "nt" else "pan"
    try:
        subprocess.run(["gcc", "-o", exe_file, "pan.c"],
                       cwd=upload_dir, check=True,
                       capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        return f"Compilation failed:\n{e.stderr or e.stdout}"

    # Step 3: ./pan -a
    exe_path = exe_file if os.name == "nt" else f"./{exe_file}"
    try:
        result = subprocess.run([exe_path, "-a"],
                                cwd=upload_dir, capture_output=True, text=True)
        output_text = result.stdout or result.stderr
    except FileNotFoundError:
        return "Error: pan.exe not found."
    except subprocess.CalledProcessError as e:
        output_text = e.stdout + "\n" + e.stderr

    # Summarize result
    if "acceptance cycle" in output_text or "errors: 1" in output_text:
        summary = "⚠️ Liveness violation detected (acceptance cycle found)."
    elif "errors: 0" in output_text:
        summary = "✅ No liveness violations detected."
    else:
        summary = "ℹ️ Could not determine liveness result."

    return f"{summary}\n\nFiles saved in: {upload_dir}\n\n--- SPIN Output ---\n{output_text}"


# Addestramento della voce Piper del personaggio (tools/piper_voice_dataset.py produce i dati).
# Il training di Piper e' supportato solo su Linux: qui gira in un container con la GPU di Docker Desktop.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake ninja-build git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/OHF-voice/piper1-gpl.git /piper
WORKDIR /piper
RUN pip install --no-cache-dir scikit-build
RUN pip install --no-cache-dir -e '.[train]'
RUN ./build_monotonic_align.sh && python setup.py build_ext --inplace

FROM python:3.11.9-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    verilator=5.020-1 \
    iverilog=11.3-1.1 \
    make=4.3-4.1 \
    git=1:2.39.5-0+deb12u2 \
    ca-certificates=20230311 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /work
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /work
ENV PYTHONPATH=/work
ENV PATH="/work:${PATH}"

# Default: print help. Real entry is python3 -m verif_agent
ENTRYPOINT ["python3", "-m", "verif_agent"]
CMD ["--help"]

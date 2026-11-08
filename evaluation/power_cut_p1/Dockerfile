# The writer of power-cut study P1 (#190) as a container on the box whose power is cut.
FROM python:3.12.10-slim-bookworm
RUN pip install --no-cache-dir "psycopg[binary]==3.2.10" "numpy==2.2.6"
COPY evaluation/power_cut_p1/writer.py /opt/pc/writer.py
WORKDIR /opt/pc
USER nobody
ENTRYPOINT ["python", "-u", "writer.py"]

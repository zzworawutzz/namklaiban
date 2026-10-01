# น้ำใกล้บ้านฉัน

ดูระดับน้ำจากสถานีวัดที่ใกล้บ้านที่สุด ข้อมูลจาก ThaiWater (สสน.) ใช้ประกอบการตัดสินใจเท่านั้น ไม่ใช่ประกาศทางการ

| ไฟล์ | หน้าที่ |
|---|---|
| `ingest.py` | ดึงข้อมูล ThaiWater → ฐานข้อมูล, ลบข้อมูลเก่าเกิน 31 วัน, บันทึกผลการรัน |
| `db.py` | ชั้นเชื่อมต่อ: SQLite (ในเครื่อง/เทสต์) หรือ Postgres เมื่อมี `DATABASE_URL` |
| `core.py` | ตรรกะอ่านข้อมูล: stale, แนวโน้ม, เวลาถึงตลิ่ง, คำแนะนำ, ต้นน้ำ/ปลายน้ำ |
| `api.py` | FastAPI + endpoint `/api/cron/ingest` (ป้องกันด้วย `CRON_SECRET`) |
| `notify.py` | แจ้งเตือนเมื่อสถานะสถานีใกล้บ้านเปลี่ยน (LINE) |
| `public/` | หน้าเว็บ (Leaflet) + PWA (Vercel เสิร์ฟจาก CDN) |

## รันในเครื่อง
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python ingest.py
.venv/bin/uvicorn api:app --reload      # เปิด http://127.0.0.1:8000
.venv/bin/python -m pytest
```
ตั้ง cron ให้รัน `ingest.py` และ `notify.py run` ทุก 20 นาที

## API
`/health` · `/stations?province=&status=` · `/stations/nearby?lat=&lng=` · `/stations/{id}/readings?days=` · `/stations/{id}/related`

ฟิลด์เพิ่มของสถานี: `trend` (rising/falling/steady), `trend_pct_per_hr`, `eta_to_bank_h`, `advice`, `watch_pct`, `alert_pct`
- แนวโน้มคำนวณจากการวัดย้อนหลัง 6 ชม. ต้องมีข้อมูลห่างกันอย่างน้อย 45 นาที ถึงจะมีค่า
- `eta_to_bank_h` เป็นการต่อเส้นตรงจากอัตราปัจจุบัน ไม่ใช่การพยากรณ์
- `/related` แยกต้นน้ำ/ปลายน้ำจากระดับท้องน้ำของสถานีที่อยู่แม่น้ำเดียวกันในรัศมี 150 กม. เป็นการประมาณ

## Threshold ต่อสถานี
คัดลอก `thresholds.example.json` เป็น `thresholds.json` แล้วรัน `python ingest.py --thresholds thresholds.json` (ค่าเริ่มต้น 70% เฝ้าระวัง / 90% เตือนภัย)

## แจ้งเตือน LINE
1. สร้าง LINE Official Account เปิด Messaging API แล้วออก channel access token
2. `export LINE_CHANNEL_TOKEN=...`
3. `python notify.py add --channel line --target <LINE userId> --lat 14.2 --lng 99.0 --label บ้าน`
4. `python notify.py run` (ใส่ cron ต่อจาก ingest)

ลองโดยไม่ต้องมีบัญชีได้ด้วย `--channel stdout` แจ้งครั้งแรกเฉพาะเมื่อสถานีอยู่ที่เฝ้าระวัง/เตือนภัยแล้ว หลังจากนั้นแจ้งทุกครั้งที่สถานะเปลี่ยน

## ฐานข้อมูล
ไม่ตั้งอะไร = SQLite ที่ `water.db` (หรือ `WATER_DB`) ตั้ง `DATABASE_URL=postgresql://...` = Postgres ชุดทดสอบ `test_postgres.py` รันกับ Postgres จริง (ผ่านแพ็กเกจ `pgserver`)

## Deploy บน Vercel + Neon (Postgres)
1. push โปรเจกต์ขึ้น GitHub แล้ว Import ที่ Vercel (ไฟล์ `pyproject.toml` ชี้ entrypoint, `vercel.json` ตั้ง region `sin1`)
2. Vercel → Storage / Marketplace → เพิ่ม **Neon** ให้โปรเจกต์ จะได้ env `DATABASE_URL` มาเอง (ควรเป็น URL แบบ pooled) และเลือก region สิงคโปร์ให้ตรงกับฟังก์ชัน
3. ตั้ง env เพิ่มใน Vercel: `CRON_SECRET` (สตริงสุ่ม ≥16 ตัว) และถ้าจะแจ้งเตือน `LINE_CHANNEL_TOKEN`
4. Deploy แล้วตั้งตัวเรียก `/api/cron/ingest` ทุก 20 นาที เลือกอย่างใดอย่างหนึ่ง:
   - **ฟรี (Hobby):** Vercel Cron ฟรีรันได้วันละครั้ง และ expression ที่ถี่กว่านั้นทำให้ deploy ล้มเหลว จึงใช้ `.github/workflows/ingest.yml` แทน: ตั้ง repo secret `CRON_SECRET` (ค่าเดียวกับ Vercel) และ repo variable `APP_URL`
   - **Pro:** เพิ่มใน `vercel.json` แล้ว Vercel จะส่ง `Authorization: Bearer $CRON_SECRET` ให้เอง
     ```json
     "crons": [{ "path": "/api/cron/ingest", "schedule": "*/20 * * * *" }]
     ```
5. เปิด `https://<โดเมน>/health` ดู `ingest_ok` ต้องเป็น `true` หลังรันรอบแรก

เพิ่มผู้รับแจ้งเตือน (รันจากเครื่องคุณ ชี้ DB ของ Neon):
```bash
DATABASE_URL='postgresql://...' .venv/bin/python notify.py add --channel line --target <userId> --lat 14.2 --lng 99.0 --label บ้าน
```
ข้อควรรู้: cron ส่งซ้ำหรือพลาดได้ (best effort) ระบบจึงออกแบบให้รันซ้ำได้ปลอดภัย (ข้อมูลไม่ซ้ำ + ล็อกกันรันซ้อน)

## Deploy ด้วย Docker (ทางเลือก ใช้ SQLite + volume)
```bash
docker build -t nam-klai-baan-chan .
docker run -p 8000:8000 -v nkb-data:/data -e LINE_CHANNEL_TOKEN=... nam-klai-baan-chan
```
`INGEST_EVERY` (วินาที, ค่าเริ่มต้น 1200) และ `THRESHOLDS_FILE` ปรับได้ผ่าน env ใช้ SQLite ไฟล์เดียวจึงรัน instance เดียวพร้อม volume เท่านั้น (หรือใส่ `DATABASE_URL` ให้ใช้ Postgres)

# Deploy Guide — btc-brain-ops

## 1. สร้าง GitHub repo

```bash
# ใน terminal ที่โฟลเดอร์ btc-brain-ops/
git init
git add .
git commit -m "btc-brain-ops v1.0.0 — Phase 1"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/btc-brain-ops.git
git push -u origin main
```

## 2. สร้าง Railway project

1. ไปที่ railway.app → New Project
2. เลือก **Deploy from GitHub repo**
3. เลือก repo `btc-brain-ops`
4. Railway จะ detect `Procfile` และ build อัตโนมัติ

## 3. เพิ่ม Volume (สำคัญ — ไม่งั้น DB หายทุก deploy)

1. ใน Railway project → **Add Volume**
2. Mount path: `/data`
3. ไปที่ **Variables** → เพิ่ม:
   ```
   DB_PATH = /data/btc_brain_ops.db
   ```

## 4. ตรวจสอบ deploy

```bash
# เปิด URL ที่ Railway ให้
curl https://your-app.railway.app/health
# → {"status": "ok"}

curl https://your-app.railway.app/
# → system info + endpoints
```

## 5. เพิ่ม 1 บรรทัดใน production bot

ใน `btc_alert_bot_v7.py` หลังส่ง Telegram alert:

```python
import httpx

def report_to_ops(direction, entry, tp, sl, session, week,
                  regime=None, oi_state=None, rsi=None, trend_4h=None):
    try:
        httpx.post(
            "https://your-app.railway.app/signals/ingest",
            json={
                "week": week,
                "direction": direction,
                "entry_price": entry,
                "tp_price": tp,
                "sl_price": sl,
                "session": session,
                "regime": regime,
                "oi_state": oi_state,
                "rsi_at_entry": rsi,
                "trend_4h": trend_4h,
            },
            timeout=5,
        )
    except Exception:
        pass  # ถ้า ops ล่ม production ไม่กระทบ
```

## 6. ปิด trade — บันทึก PnL

```bash
curl -X POST https://your-app.railway.app/signals/1/close \
  -H "Content-Type: application/json" \
  -d '{
    "result": "WIN",
    "exit_price": 68500,
    "net_pnl_usd": 45.20,
    "fee_usd": 3.80,
    "rr_achieved": 1.0
  }'
```

## 7. ดู PnL summary รายสัปดาห์

```bash
curl https://your-app.railway.app/signals/stats/week/W20
```

## 8. Export weekly intelligence (วันเสาร์)

```bash
# Generate
curl -X POST https://your-app.railway.app/weekly/W20/generate

# Get markdown → copy ให้ ChatGPT วิเคราะห์
curl https://your-app.railway.app/weekly/W20

# Get Obsidian format → paste ใน vault
curl https://your-app.railway.app/weekly/W20/obsidian
```

## Interactive docs

```
https://your-app.railway.app/docs
```

Swagger UI — ทดสอบทุก endpoint ได้เลยในหน้าเว็บ

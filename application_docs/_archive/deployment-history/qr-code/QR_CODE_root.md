# TripSync QR Code

Current demo QR code target:

```text
http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com
```

Generated files:

```text
docs/assets/tripsync-current-url-qr.png
docs/assets/tripsync-current-url-qr-large.png
```

Use the large version for slides, posters, printed handouts, or classroom demos.

Important:

```text
This QR code points to the current temporary HTTP ALB URL.
After Phase 10 HTTPS/custom domain is completed, regenerate the QR code to point to the final HTTPS domain.
```

## How Long This QR Code Can Work

This QR code should keep working as long as the current AWS Application Load Balancer still exists:

```text
tripsync-backend-alb
```

In practical terms:

```text
If the ALB is kept running, the current QR code can continue working for the demo period.
If the ALB is deleted and recreated, the AWS-generated DNS name may change, and this QR code will stop working.
If the ECS services are stopped or unhealthy, the QR code may still open the URL but the app may not load correctly.
If the project cleans up AWS resources to reduce cost, this QR code should be treated as expired.
```

Use this current QR code for short-term demo/testing only.

For a final presentation or long-term shared link, use a custom HTTPS domain instead:

```text
https://<your-domain>
```

Then regenerate the QR code after the custom domain is working.

Recommended final QR target after custom domain:

```text
https://<your-domain>
```

Regenerate command:

```powershell
python -m pip install --user "qrcode[pil]"
@'
import qrcode
from pathlib import Path

url = "https://<your-domain>"
out_dir = Path("docs/assets")
out_dir.mkdir(parents=True, exist_ok=True)

qr = qrcode.QRCode(
    version=None,
    error_correction=qrcode.constants.ERROR_CORRECT_Q,
    box_size=12,
    border=4,
)
qr.add_data(url)
qr.make(fit=True)
img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
img.save(out_dir / "tripsync-final-domain-qr.png")
img.resize((1600, 1600)).save(out_dir / "tripsync-final-domain-qr-large.png")
'@ | python -
```

# Kuro Kainos — Android & iOS (Capacitor)

The app is a PWA; Capacitor wraps that same web code in a native shell for the
Play Store and App Store, and adds native push notifications, geolocation, and
share. No rewrite — the native app loads the web app.

## One-time setup
```bash
npm install
npx cap add android      # needs Android Studio + SDK (JDK 17)
npx cap add ios          # needs a Mac + Xcode
```

`capacitor.config.json` currently points `server.url` at the **live** GitHub
Pages site, so the native app auto-updates with each deploy (like the current
TWA) — fastest for testing. For a **store release**, bundle the assets instead:
remove `server.url`, run `npm run mkwww` (copies the web files + `data/` into
`www/`), then `npx cap sync`. Bundled content + the native features below is what
gets past Apple's "it's just a website" review.

## Build / run
```bash
npm run android      # builds www, syncs, opens Android Studio → Run
npm run ios          # builds www, syncs, opens Xcode → Run
```

## Native features to wire in the shell
Add to the Capacitor bundle (these are no-ops in a plain browser, so keep them
out of `app.js` and load them only in the native build):

```js
import { PushNotifications } from '@capacitor/push-notifications';

export async function initPush() {
  const perm = await PushNotifications.requestPermissions();
  if (perm.receive !== 'granted') return;
  await PushNotifications.register();                       // -> FCM/APNs token
  PushNotifications.addListener('registration', t =>
    fetch('https://<your-worker>/register-device', {         // store token server-side
      method: 'POST', body: JSON.stringify({ token: t.value }) }));
  PushNotifications.addListener('pushNotificationReceived', n => console.log(n));
}
```

Background price-drop pushes (app closed) need a sender: a tiny scheduled job
(e.g. extend the Cloudflare Worker) that, after each pipeline run, diffs the new
cheapest-per-area against the last and sends FCM/APNs to subscribed tokens. The
in-app alert (`checkPriceAlerts()` in `app.js`) already covers the app-open case.

## Stores
- **Android:** Google Play Console — **$25 one-time**. `npx cap open android` → build signed AAB.
- **iOS:** Apple Developer — **$99/year** + a Mac. `npx cap open ios` → archive → upload.
- Reuse the existing keystore (see the Android NFC/APK notes) if migrating from the TWA.

## Data licence
Prices are from LEA (ena.lt) under **CC BY 4.0** — keep the attribution shown in the footer.

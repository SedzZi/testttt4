package org.lan.lanmobile;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

/**
 * Telefon açıldığında (BOOT_COMPLETED) veya uygulama güncellendiğinde
 * arka plan LAN servisini otomatik başlatır.
 */
public class BootReceiver extends BroadcastReceiver {

    private static final String TAG = "LANBootReceiver";

    @Override
    public void onReceive(Context context, Intent intent) {
        try {
            // p4a'nın ürettiği servis intent'ini (gerekli extras ile) kur
            Intent service = ServiceLanengine.getDefaultIntent(
                    context, "", "LAN", "Ağ dinlemesi açık", "boot");
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(service);
            } else {
                context.startService(service);
            }
            Log.i(TAG, "LAN servisi başlatıldı");
        } catch (Exception e) {
            Log.w(TAG, "Servis başlatılamadı", e);
        }
    }
}
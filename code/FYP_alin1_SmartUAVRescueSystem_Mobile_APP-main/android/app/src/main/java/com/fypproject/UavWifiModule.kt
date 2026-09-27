package com.fypproject

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build
import java.util.UUID
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.modules.core.DeviceEventManagerModule


class UavWifiModule(
    private val reactContext: ReactApplicationContext,
) : ReactContextBaseJavaModule(reactContext) {

  companion object {
    private const val MODULE_NAME = "UavWifi"
    private const val CONNECT_TIMEOUT_MS = 45_000
    private const val EVENT_WIFI_LOST = "UavWifiLost"
  }

  private val connectivityManager =
      reactContext.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
  private val stateLock = Any()
  private var activeCallback: ConnectivityManager.NetworkCallback? = null
  private var pendingPromise: Promise? = null
  private var connectionLost = false

  override fun getName(): String = MODULE_NAME

  @ReactMethod
  fun createCaptureId(promise: Promise) {
    promise.resolve(UUID.randomUUID().toString().replace("-", ""))
  }

  @ReactMethod
  fun connect(ssidValue: String, passphraseValue: String?, promise: Promise) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
      promise.reject(
          "UNSUPPORTED_ANDROID_VERSION",
          "System-confirmed UAV Wi-Fi selection requires Android 10 or later.",
      )
      return
    }

    val ssid = ssidValue.trim()
    val passphrase = passphraseValue ?: ""
    if (ssid.isEmpty()) {
      promise.reject("INVALID_WIFI_CONFIG", "UAV Wi-Fi SSID is required.")
      return
    }
    if (passphrase.isNotEmpty() && passphrase.length !in 8..63) {
      promise.reject(
          "INVALID_WIFI_CONFIG",
          "A WPA2 passphrase must contain between 8 and 63 characters.",
      )
      return
    }

    val callback: ConnectivityManager.NetworkCallback
    try {
      val specifierBuilder = WifiNetworkSpecifier.Builder().setSsid(ssid)
      if (passphrase.isNotEmpty()) {
        specifierBuilder.setWpa2Passphrase(passphrase)
      }
      val request =
          NetworkRequest.Builder()
              .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
              .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
              .setNetworkSpecifier(specifierBuilder.build())
              .build()

      synchronized(stateLock) {
        if (activeCallback != null) {
          promise.reject(
              "CONNECTION_IN_PROGRESS",
              "A UAV Wi-Fi request is already active.",
          )
          return
        }
        pendingPromise = promise
        connectionLost = false
        callback = createCallback(ssid)
        activeCallback = callback
      }
      connectivityManager.requestNetwork(request, callback, CONNECT_TIMEOUT_MS)
    } catch (error: SecurityException) {
      rejectAndRelease(
          promise,
          "WIFI_PERMISSION_DENIED",
          "Android denied the UAV Wi-Fi request.",
          error,
      )
    } catch (error: IllegalArgumentException) {
      rejectAndRelease(
          promise,
          "INVALID_WIFI_CONFIG",
          "The UAV Wi-Fi SSID or passphrase is invalid.",
          error,
      )
    } catch (error: RuntimeException) {
      rejectAndRelease(
          promise,
          "WIFI_REQUEST_FAILED",
          "Android could not start the UAV Wi-Fi request.",
          error,
      )
    }
  }

  @ReactMethod
  fun disconnect(promise: Promise) {
    val restored = synchronized(stateLock) {
      releaseLocked(
          rejectPendingCode = "CONNECTION_CANCELLED",
          rejectPendingMessage = "The UAV Wi-Fi request was cancelled.",
      )
    }
    if (restored) {
      promise.resolve(null)
    } else {
      promise.reject(
          "NETWORK_UNBIND_FAILED",
          "Android could not restore the default network after UAV Wi-Fi.",
      )
    }
  }

  private fun createCallback(ssid: String): ConnectivityManager.NetworkCallback {
    return object : ConnectivityManager.NetworkCallback() {
      override fun onAvailable(network: Network) {
        val promiseToResolve: Promise?
        synchronized(stateLock) {
          if (activeCallback !== this || connectionLost) return
          if (!connectivityManager.bindProcessToNetwork(network)) {
            val promiseToReject = pendingPromise
            pendingPromise = null
            releaseLocked()
            promiseToReject?.reject(
                "NETWORK_BIND_FAILED",
                "Android selected the UAV Wi-Fi but could not bind app traffic to it.",
            )
            return
          }
          promiseToResolve = pendingPromise
          pendingPromise = null
        }
        val result = Arguments.createMap()
        result.putString("ssid", ssid)
        result.putBoolean("processBound", true)
        promiseToResolve?.resolve(result)
      }

      override fun onUnavailable() {
        val promiseToReject: Promise?
        synchronized(stateLock) {
          if (activeCallback !== this) return
          promiseToReject = pendingPromise
          pendingPromise = null
          releaseLocked()
        }
        promiseToReject?.reject(
            "WIFI_UNAVAILABLE",
            "The UAV Wi-Fi request was declined, timed out, or the network was unavailable.",
        )
      }

      override fun onLost(network: Network) {
        var shouldEmit = false
        synchronized(stateLock) {
          if (activeCallback === this && !connectionLost) {


            connectionLost = true
            shouldEmit = true
          }
        }
        if (shouldEmit) emitWifiLost()
      }
    }
  }

  private fun emitWifiLost() {
    reactContext
        .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
        .emit(EVENT_WIFI_LOST, null)
  }

  private fun rejectAndRelease(
      promise: Promise,
      code: String,
      message: String,
      error: Throwable,
  ) {
    synchronized(stateLock) {
      if (pendingPromise === promise) {
        pendingPromise = null
      }
      releaseLocked()
    }
    promise.reject(code, message, error)
  }

  private fun releaseLocked(
      rejectPendingCode: String? = null,
      rejectPendingMessage: String? = null,
  ): Boolean {
    val callbackToRelease = activeCallback
    activeCallback = null
    connectionLost = false
    callbackToRelease?.let { callback ->
      try {
        connectivityManager.unregisterNetworkCallback(callback)
      } catch (_: IllegalArgumentException) {

      }
    }
    val restored = connectivityManager.bindProcessToNetwork(null)
    if (rejectPendingCode != null && rejectPendingMessage != null) {
      pendingPromise?.reject(rejectPendingCode, rejectPendingMessage)
      pendingPromise = null
    }
    return restored
  }

  override fun invalidate() {
    synchronized(stateLock) {
      releaseLocked(
          rejectPendingCode = "MODULE_DESTROYED",
          rejectPendingMessage = "The UAV Wi-Fi module was closed.",
      )
    }
    super.invalidate()
  }
}

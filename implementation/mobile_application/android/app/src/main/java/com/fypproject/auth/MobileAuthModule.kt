package com.fypproject.auth

import android.os.CancellationSignal
import androidx.credentials.ClearCredentialStateRequest
import androidx.credentials.CredentialManager
import androidx.credentials.CredentialManagerCallback
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import androidx.credentials.GetCredentialResponse
import androidx.credentials.exceptions.ClearCredentialException
import androidx.credentials.exceptions.GetCredentialCancellationException
import androidx.credentials.exceptions.GetCredentialException
import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.modules.core.DeviceEventManagerModule
import com.facebook.react.uimanager.ViewManager
import com.fypproject.tracking.TrackingStore
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.auth.GoogleAuthProvider
import java.util.concurrent.Executors

class MobileAuthModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
    private val worker = Executors.newSingleThreadExecutor()
    private var observedAuth: FirebaseAuth? = null
    private val authListener = FirebaseAuth.AuthStateListener { emitState() }
    private var credentialCancellation: CancellationSignal? = null
    override fun getName() = "MobileAuth"

    override fun initialize() {
        super.initialize()
        runCatching {
            MobileIdentity.auth(context).also { observedAuth = it; it.addAuthStateListener(authListener) }
        }
    }

    override fun invalidate() {
        credentialCancellation?.cancel()
        observedAuth?.removeAuthStateListener(authListener)
        worker.shutdown()
        super.invalidate()
    }

    @ReactMethod fun addListener(eventName: String) { /* Required by NativeEventEmitter. */ }
    @ReactMethod fun removeListeners(count: Int) { /* No per-JavaScript listener state is retained. */ }

    @ReactMethod fun state(promise: Promise) = perform(promise) { MobileIdentity.state(context).toString() }

    @ReactMethod fun refreshBinding(promise: Promise) = perform(promise) {
        MobileIdentity.refreshBinding(context)
        emitState()
        MobileIdentity.state(context).toString()
    }

    @ReactMethod fun signIn(promise: Promise) {
        val activity = currentActivity
        if (activity == null) {
            promise.reject("AUTH_ACTIVITY", "Open the app before signing in.")
            return
        }
        worker.execute {
            var ownsAccountChange = false
            try {
                val resource = context.resources.getIdentifier("default_web_client_id", "string", context.packageName)
                val clientId = if (resource == 0) "" else context.getString(resource)
                if (!clientId.endsWith(".apps.googleusercontent.com")) {
                    throw AuthException("AUTH_CONFIGURATION", "Configure Google sign-in and download the updated Android Firebase configuration.")
                }
                MobileIdentity.app(context)
                beginAccountChange()
                ownsAccountChange = true
                val request = GetCredentialRequest.Builder().addCredentialOption(
                    GetGoogleIdOption.Builder().setServerClientId(clientId)
                        .setFilterByAuthorizedAccounts(false).setAutoSelectEnabled(false).build()
                ).build()
                val cancellation = CancellationSignal().also { credentialCancellation = it }
                activity.runOnUiThread {
                    try {
                        CredentialManager.create(context).getCredentialAsync(activity, request, cancellation, worker,
                            object : CredentialManagerCallback<GetCredentialResponse, GetCredentialException> {
                                override fun onResult(result: GetCredentialResponse) {
                                    try {
                                        val credential = result.credential
                                        if (credential !is CustomCredential || credential.type != GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL) {
                                            throw AuthException("AUTH_CREDENTIAL", "Choose a Google account to sign in.")
                                        }
                                        val google = GoogleIdTokenCredential.createFrom(credential.data)
                                        MobileIdentity.clearBinding(context)
                                        MobileIdentity.auth(context).signInWithCredential(
                                            GoogleAuthProvider.getCredential(google.idToken, null)
                                        ).addOnCompleteListener(worker) { task ->
                                            try {
                                                if (!task.isSuccessful) throw AuthException("AUTH_SIGN_IN", "Google sign-in could not be completed. Check your connection and Firebase setup.")
                                                MobileIdentity.refreshBinding(context)
                                                finishAccountChange()
                                                promise.resolve(MobileIdentity.state(context).toString())
                                            } catch (error: Exception) { finishAccountChange(); reject(promise, error) }
                                        }
                                    } catch (error: Exception) { finishAccountChange(); reject(promise, error) }
                                }
                                override fun onError(error: GetCredentialException) {
                                    finishAccountChange()
                                    if (error is GetCredentialCancellationException) promise.reject("AUTH_CANCELLED", "Sign-in was cancelled.")
                                    else promise.reject("AUTH_SIGN_IN", "Google sign-in is unavailable. Check the device's Google services and app configuration.")
                                }
                            })
                    } catch (error: Exception) { finishAccountChange(); reject(promise, error) }
                }
            } catch (error: Exception) {
                if (ownsAccountChange) finishAccountChange()
                reject(promise, error)
            }
        }
    }

    @ReactMethod fun signOut(promise: Promise) = perform(promise) {
        beginAccountChange()
        try {
            MobileIdentity.auth(context).signOut()
            MobileIdentity.clearBinding(context)
            // Clear the provider's selection state so the next sign-in shows an account choice.
            CredentialManager.create(context).clearCredentialStateAsync(ClearCredentialStateRequest(), null, worker,
                object : CredentialManagerCallback<Void?, ClearCredentialException> {
                    override fun onResult(result: Void?) { }
                    override fun onError(error: ClearCredentialException) { }
                })
            true
        } finally { finishAccountChange() }
    }

    private fun beginAccountChange() {
        val store = TrackingStore.get(context)
        synchronized(store) {
            if (store.hasActiveSessionForAccountChange()) {
                throw AuthException("AUTH_RECORDING_ACTIVE", "Stop the current recording before switching or signing out of an account.")
            }
            MobileIdentity.beginAccountChange()
        }
    }

    private fun finishAccountChange() {
        credentialCancellation = null
        MobileIdentity.finishAccountChange()
        emitState()
    }

    private fun perform(promise: Promise, action: () -> Any?) {
        worker.execute { try { promise.resolve(action()) } catch (error: Exception) { reject(promise, error) } }
    }

    private fun reject(promise: Promise, error: Exception) {
        if (error is AuthException) promise.reject(error.code, error.message)
        else promise.reject("AUTH_ERROR", "The account operation could not be completed. Check the app configuration and connection.")
    }

    private fun emitState() {
        if (context.hasActiveReactInstance()) runCatching {
            context.getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
                .emit("MobileAuthChanged", MobileIdentity.state(context).toString())
        }
    }
}

class AuthPackage : ReactPackage {
    override fun createNativeModules(context: ReactApplicationContext): List<NativeModule> = listOf(MobileAuthModule(context))
    override fun createViewManagers(context: ReactApplicationContext): List<ViewManager<*, *>> = emptyList()
}

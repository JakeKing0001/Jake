package com.jake.companion.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.jake.companion.connection.ConnectionState
import com.jake.companion.repository.JakeRepository
import com.jake.companion.ui.devices.DevicesScreen
import com.jake.companion.ui.home.HomeScreen
import com.jake.companion.ui.notification.NotificationDetailScreen
import com.jake.companion.ui.pairing.PairingScreen
import com.jake.companion.ui.theme.JakeCompanionTheme

/** Le sole quattro schermate di questo MVP (F7.2: "evita... UI complessa") - un semplice stato locale invece di
 * Navigation-Compose: non serve altro per un flusso lineare pairing -> home -> dettaglio notifica/dispositivi. */
private enum class Screen { PAIRING, HOME, NOTIFICATION_DETAIL, DEVICES }

@Composable
fun JakeApp(repository: JakeRepository) {
    val state by repository.uiState.collectAsState()
    var screen by remember { mutableStateOf(if (state.connection == ConnectionState.Unpaired) Screen.PAIRING else Screen.HOME) }

    JakeCompanionTheme {
        when (screen) {
            Screen.PAIRING -> PairingScreen(
                repository = repository,
                onPaired = {
                    repository.start()
                    screen = Screen.HOME
                },
            )

            Screen.HOME -> HomeScreen(
                state = state,
                onSendMessage = repository::sendCommand,
                onOpenDecision = { screen = Screen.NOTIFICATION_DETAIL },
                onOpenDevices = { screen = Screen.DEVICES },
            )

            Screen.NOTIFICATION_DETAIL -> {
                val decision = state.pendingDecision
                if (decision == null) {
                    screen = Screen.HOME
                } else {
                    NotificationDetailScreen(
                        decision = decision,
                        onDecision = { approve ->
                            repository.resolveDecision(decision, approve)
                            screen = Screen.HOME
                        },
                        onBack = { screen = Screen.HOME },
                    )
                }
            }

            Screen.DEVICES -> {
                val thisDeviceId = (state.connection as? ConnectionState.Online)?.deviceId
                DevicesScreen(
                    repository = repository,
                    thisDeviceId = thisDeviceId,
                    onRevoked = { screen = Screen.PAIRING },
                    onBack = { screen = Screen.HOME },
                )
            }
        }
    }
}

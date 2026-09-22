package com.jake.companion.ui.devices

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jake.companion.protocol.DeviceInfoDto
import com.jake.companion.repository.JakeRepository

/**
 * "Lista dispositivi e possibilita' di scollegare/revocare QUESTO telefono" (F7.2.1) - la lista viene da
 * `GET /devices` (`core/companion_server.py`, i dispositivi ACCOPPIATI, mai un token). La revoca resta
 * self-only lato server (`POST /devices/<id>/revoke`): questa schermata non offre un pulsante per revocare UN
 * ALTRO dispositivo, coerente con quello che il server accetterebbe comunque.
 */
@Composable
fun DevicesScreen(repository: JakeRepository, thisDeviceId: String?, onRevoked: () -> Unit, onBack: () -> Unit) {
    var devices by remember { mutableStateOf<List<DeviceInfoDto>>(emptyList()) }
    var showConfirmRevoke by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) { repository.listDevices { devices = it } }

    Column(modifier = Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        TextButton(onClick = onBack) { Text("< Indietro") }
        Text("Dispositivi accoppiati")
        LazyColumn(modifier = Modifier.fillMaxWidth()) {
            items(devices) { device -> DeviceRow(device, isThisDevice = device.device_id == thisDeviceId) }
        }
        Button(onClick = { showConfirmRevoke = true }) { Text("Scollega e revoca questo telefono") }
    }

    if (showConfirmRevoke) {
        AlertDialog(
            onDismissRequest = { showConfirmRevoke = false },
            title = { Text("Confermi?") },
            text = { Text("Questo telefono perdera' l'accesso a Jake. Dovrai rifare il pairing per ricollegarlo.") },
            confirmButton = {
                TextButton(onClick = {
                    showConfirmRevoke = false
                    repository.revokeThisDevice { onRevoked() }
                }) { Text("Revoca") }
            },
            dismissButton = { TextButton(onClick = { showConfirmRevoke = false }) { Text("Annulla") } },
        )
    }
}

@Composable
private fun DeviceRow(device: DeviceInfoDto, isThisDevice: Boolean) {
    Card(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text((if (isThisDevice) "* " else "") + (device.name.ifBlank { device.device_id }))
            Text("Stato: ${device.status}")
        }
    }
}

package com.jake.companion.ui.home

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.weight
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.IconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.List
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jake.companion.connection.ConnectionState
import com.jake.companion.repository.ChatMessage
import com.jake.companion.repository.UiState

/**
 * Schermata principale (F7.2: "stato di Jake: online/offline, task corrente, sessione, ultimo messaggio" + "chat
 * mobile che usa /command"). Riceve `UiState` gia' pronto (prodotto da [com.jake.companion.repository.reduce]):
 * questa funzione mostra soltanto, non decide nulla.
 */
@Composable
fun HomeScreen(
    state: UiState,
    onSendMessage: (String) -> Unit,
    onOpenDecision: () -> Unit,
    onOpenDevices: () -> Unit,
) {
    Column(modifier = Modifier.fillMaxSize()) {
        TopAppBar(
            title = { Text("Jake") },
            actions = {
                IconButton(onClick = onOpenDevices) { Icon(Icons.Filled.List, contentDescription = "Dispositivi") }
            },
        )
        ConnectionBanner(state.connection)
        state.currentTaskLabel?.let { label ->
            Card(modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp)) {
                Text("In corso: $label (${state.currentTaskStatus ?: ""})", modifier = Modifier.padding(12.dp))
            }
        }
        state.pendingDecision?.let { decision ->
            Card(modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp)) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text("Decisione richiesta: ${decision.label}")
                    Text(decision.situation)
                    Button(onClick = onOpenDecision) { Text("Vedi dettagli e rispondi") }
                }
            }
        }
        state.lastError?.let { error -> Text(error, modifier = Modifier.padding(horizontal = 12.dp)) }

        LazyColumn(modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 12.dp)) {
            items(state.messages) { message -> ChatBubble(message) }
        }

        MessageInput(onSend = onSendMessage)
    }
}

@Composable
private fun ConnectionBanner(connection: ConnectionState) {
    val text = when (connection) {
        is ConnectionState.Unpaired -> "Non collegato"
        is ConnectionState.Connecting -> "Connessione in corso..."
        is ConnectionState.Online -> "Online - sessione ${connection.sessionId}"
        is ConnectionState.Offline -> "Offline: ${connection.reason}"
        is ConnectionState.Reconnecting -> "Riconnessione in corso (tentativo ${connection.attempt})..."
    }
    Text(text, modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp))
}

@Composable
private fun ChatBubble(message: ChatMessage) {
    val prefix = if (message.role == "user") "Tu" else "Jake"
    Text("$prefix: ${message.text}", modifier = Modifier.padding(vertical = 4.dp))
}

@Composable
private fun MessageInput(onSend: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    Row(modifier = Modifier.fillMaxWidth().padding(12.dp)) {
        OutlinedTextField(value = text, onValueChange = { text = it }, modifier = Modifier.weight(1f))
        Spacer(modifier = Modifier.height(0.dp))
        Button(onClick = {
            val toSend = text.trim()
            if (toSend.isNotEmpty()) {
                onSend(toSend)
                text = ""
            }
        }, modifier = Modifier.padding(start = 8.dp)) { Text("Invia") }
    }
}

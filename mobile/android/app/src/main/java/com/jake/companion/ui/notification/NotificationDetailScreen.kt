package com.jake.companion.ui.notification

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.weight
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Divider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jake.companion.protocol.ActionDoneDto
import com.jake.companion.repository.PendingDecision

/**
 * "Schermata della notifica con situazione, azioni gia' eseguite, evidenza disponibile e decisione richiesta"
 * (F7.2) - ogni campo qui viene GIA' dal payload del PC (`TaskNotificationPayload`, F6.3/F6.7): questa schermata
 * non aggiunge un solo bit di valutazione propria, mostra solo cio' che Jake ha gia' deciso e chiesto.
 * `Approve`/`Deny` chiamano [onDecision] - la risoluzione vera passa da `POST /approvals/<task_id>`
 * (`JakeRepository.resolveDecision`), la STESSA pipeline di conferma del PC.
 */
@Composable
fun NotificationDetailScreen(decision: PendingDecision, onDecision: (approve: Boolean) -> Unit, onBack: () -> Unit) {
    Column(modifier = Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        TextButton(onClick = onBack) { Text("< Indietro") }
        Text("Compito: ${decision.label}")
        Text("Sessione: ${decision.sessionId ?: "-"}   ·   Task: ${decision.taskId}")

        Divider()
        Text("Situazione")
        Text(decision.situation)

        Divider()
        Text("Azioni gia' eseguite (${decision.actionsDone.size})")
        if (decision.actionsDone.isEmpty()) {
            Text("Nessuna azione ancora eseguita in questo compito.")
        } else {
            LazyColumn(modifier = Modifier.fillMaxWidth()) {
                items(decision.actionsDone) { action -> ActionRow(action) }
            }
        }

        decision.decisionRequired?.intent?.let { intent ->
            Divider()
            Text("Decisione richiesta")
            Text("Azione: $intent")
            decision.decisionRequired.policy_reason?.let { reason -> Text("Motivo della policy: $reason") }
        }

        Divider()
        Text("Valutazione di Jake: ${decision.decision.reason}")
        Text("Priorita': ${decision.decision.priority.toInt()}   ·   Canale: ${decision.decision.channel ?: "-"}")

        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(onClick = { onDecision(true) }, modifier = Modifier.weight(1f)) { Text("Approve") }
            Button(onClick = { onDecision(false) }, modifier = Modifier.weight(1f)) { Text("Deny") }
        }
    }
}

@Composable
private fun ActionRow(action: ActionDoneDto) {
    val mark = if (action.success) "OK" else "X"
    Text("[$mark] ${action.intent}")
}

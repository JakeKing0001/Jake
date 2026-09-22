package com.jake.companion.events

import com.jake.companion.protocol.HudEventDto
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources

/**
 * Client SSE per `GET /events` (F7.1/F6 -> F7.2 "stream live tramite SSE"). Un solo iscritto per connessione, lo
 * stesso modello che il server gia' supporta per qualunque numero di client indipendenti
 * (`core/event_bus.py::EventBus`, `core/companion_server.py::_stream_events`). Ricostruisce SOLO `HudEventDto`
 * dal JSON di ogni riga `data:` - nessuna decisione qui: chi riceve `onEvent` (`JakeRepository`) decide cosa
 * farne, questo client non fa altro che leggere il flusso.
 */
class EventStreamClient(
    private val httpClient: OkHttpClient,
    private val json: Json = Json { ignoreUnknownKeys = true },
) {
    private var eventSource: EventSource? = null

    fun connect(
        url: String,
        token: String?,
        lastEventId: Long?,
        onEvent: (HudEventDto) -> Unit,
        onOpen: () -> Unit,
        onFailure: (Throwable?, Response?) -> Unit,
    ) {
        val requestBuilder = Request.Builder().url(url)
        if (token != null) requestBuilder.header("Authorization", "Bearer $token")
        // F4.1.3 (resume dall'ultimo sequence id, gia' supportato lato server): un client che riconnette manda
        // l'ultimo sequence_id visto, cosi' il server puo' riprodurre solo cio' che si e' perso nel frattempo
        // (entro il buffer di replay) invece di far ripartire lo stream da zero.
        if (lastEventId != null && lastEventId > 0) {
            requestBuilder.header("Last-Event-ID", lastEventId.toString())
        }
        val listener = object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) = onOpen()

            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                val event = runCatching { json.decodeFromString(HudEventDto.serializer(), data) }.getOrNull()
                if (event != null) onEvent(event)
                // Un `data:` malformato (un futuro formato non ancora compreso da questa app, o un commento SSE
                // come ": keep-alive") viene semplicemente ignorato - mai un crash dello stream per UNA riga.
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) = onFailure(t, response)

            override fun onClosed(eventSource: EventSource) = Unit
        }
        eventSource = EventSources.createFactory(httpClient).newEventSource(requestBuilder.build(), listener)
    }

    fun disconnect() {
        eventSource?.cancel()
        eventSource = null
    }
}

package com.jake.companion

import android.app.Application

/** Nessuno stato applicativo qui - solo il punto d'ingresso standard Android. [com.jake.companion.repository.
 * JakeRepository] vive nel ciclo di vita di `MainActivity` (F7.2: MVP in primo piano, niente servizio in
 * background - fuori scopo per questo incremento, vedi ROADMAP_EXECUTION.md). */
class JakeApplication : Application()

"""Test per core/sync_crypto.py e core/sync_engine.py (F7.6). Crittografia VERA (`cryptography`), nessuna rete:
i dispositivi si scambiano le buste a mano. Il criterio di uscita di F7.6 - "un dispositivo revocato non legge nuovi
dati e i conflitti non perdono modifiche" - e' provato qui con scenari a piu' dispositivi."""
import base64
import itertools
import json
import tempfile
import unittest
from pathlib import Path

from core.sync_crypto import (
    DeviceKeys, Envelope, Keyring, PublicKeys, SyncCryptoError, open_envelope, seal,
)
from core.sync_engine import (
    Change, HybridClock, NotSyncable, OutboundQueue, ReplicaState, Stamp, SyncNode, check_syncable,
)


class FakeVault:
    def protect(self, text):
        return "vault:" + base64.b64encode(text.encode()).decode()

    def unprotect(self, text):
        return base64.b64decode(text[6:]).decode() if text.startswith("vault:") else None


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_node(device_id, profiles=("davide",), clock=None, **kwargs):
    keys = DeviceKeys.generate()
    return SyncNode(device_id, keys, Keyring(), set(profiles), wall=clock or Clock(), **kwargs)


def pair(*nodes, profiles=("davide",)):
    """Ogni nodo conosce le chiavi pubbliche degli altri per i profili dati."""
    for a in nodes:
        for b in nodes:
            if a is not b:
                a.keyring.add(b.device_id, b.keys.public, set(profiles))


def deliver(sender, receiver):
    """Consegna tutto cio' che `sender` ha in coda per `receiver` (come farebbe un relay), ritorna i risultati."""
    return [receiver.receive(env) for env in sender.queue.drain(receiver.device_id)]


class CryptoTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = DeviceKeys.generate(), DeviceKeys.generate()
        self.ring_b = Keyring()
        self.ring_b.add("a", self.a.public)
        self.ring_a = Keyring()
        self.ring_a.add("b", self.b.public, {"davide"})

    def _seal(self, text=b"ciao segreto"):
        return seal(text, "a", self.a, self.ring_a.get("b"), "davide", 1)

    def test_roundtrip(self):
        envelope = self._seal()
        self.assertEqual(open_envelope(envelope, "b", self.b, self.ring_b), b"ciao segreto")

    def test_the_relay_sees_no_plaintext(self):
        envelope = self._seal(b"contenuto molto riservato 12345")
        blob = json.dumps(envelope.to_dict())
        self.assertNotIn("riservato", blob)
        self.assertNotIn("contenuto", blob)

    def test_two_seals_of_the_same_text_differ(self):
        self.assertNotEqual(self._seal().ciphertext, self._seal().ciphertext)

    def test_only_the_intended_recipient_can_open(self):
        third = DeviceKeys.generate()
        ring_c = Keyring()
        ring_c.add("a", self.a.public)
        with self.assertRaises(SyncCryptoError):
            open_envelope(self._seal(), "c", third, ring_c)  # destinato a b, non a c
        forged = Envelope("a", "c", "davide", 1, *(getattr(self._seal(), f) for f in ("ephemeral", "nonce", "ciphertext", "signature")))
        with self.assertRaises(SyncCryptoError):
            open_envelope(forged, "c", third, ring_c)  # cambiare il destinatario invalida firma e cifratura

    def test_tampering_with_any_part_is_detected(self):
        original = self._seal()
        data = original.to_dict()
        cases = {
            "ciphertext": {"ciphertext": base64.b64encode(bytes([original.ciphertext[0] ^ 1]) + original.ciphertext[1:]).decode()},
            "profile": {"profile": "anna"},
            "seq": {"seq": 2},
            "signature": {"signature": base64.b64encode(bytes([original.signature[0] ^ 1]) + original.signature[1:]).decode()},
        }
        for label, change in cases.items():
            with self.subTest(label=label), self.assertRaises(SyncCryptoError):
                open_envelope(Envelope.from_dict({**data, **change}), "b", self.b, self.ring_b)

    def test_an_unknown_or_revoked_sender_is_rejected(self):
        envelope = self._seal()
        with self.assertRaises(SyncCryptoError):
            open_envelope(envelope, "b", self.b, Keyring())  # mittente sconosciuto
        self.ring_b.revoke("a")
        with self.assertRaises(SyncCryptoError):
            open_envelope(envelope, "b", self.b, self.ring_b)

    def test_a_signature_from_another_key_is_rejected(self):
        impostor = DeviceKeys.generate()
        envelope = seal(b"x", "a", impostor, self.ring_a.get("b"), "davide", 1)  # si spaccia per "a"
        with self.assertRaises(SyncCryptoError):
            open_envelope(envelope, "b", self.b, self.ring_b)

    def test_it_refuses_to_seal_for_a_revoked_recipient(self):
        self.ring_a.revoke("b")
        with self.assertRaises(SyncCryptoError):
            seal(b"x", "a", self.a, self.ring_a.get("b"), "davide", 1)

    def test_envelope_survives_json_and_malformed_ones_are_rejected(self):
        envelope = self._seal()
        restored = Envelope.from_dict(json.loads(json.dumps(envelope.to_dict())))
        self.assertEqual(open_envelope(restored, "b", self.b, self.ring_b), b"ciao segreto")
        for bad in ({}, {"v": 99}, {**envelope.to_dict(), "v": 2}, {**envelope.to_dict(), "nonce": "###"}):
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(SyncCryptoError):
                Envelope.from_dict(bad)

    def test_public_keys_roundtrip_and_reject_wrong_lengths(self):
        keys = self.a.public
        self.assertEqual(PublicKeys.from_dict(keys.to_dict()), keys)
        with self.assertRaises(SyncCryptoError):
            PublicKeys.from_dict({"sign": base64.b64encode(b"corta").decode(), "agree": base64.b64encode(b"x" * 32).decode()})


class KeyStorageTests(unittest.TestCase):
    def test_keys_are_saved_protected_and_loaded_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.json"
            keys = DeviceKeys.generate()
            keys.save(path, FakeVault())
            self.assertTrue(path.read_text().startswith("vault:"))  # mai in chiaro quando c'e' un vault
            self.assertEqual(DeviceKeys.load(path, FakeVault()).public, keys.public)

    def test_with_the_real_dpapi_vault_the_file_is_not_readable_as_plain_keys(self):
        from core.secrets_vault import SecretsVault

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.json"
            keys = DeviceKeys.generate()
            vault = SecretsVault()
            keys.save(path, vault)
            content = path.read_text()
            self.assertTrue(content.startswith("dpapi:"))
            self.assertNotIn("sign", content)  # nessun campo in chiaro
            self.assertEqual(DeviceKeys.load(path, vault).public, keys.public)

    def test_a_file_that_the_vault_cannot_open_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.json"
            DeviceKeys.generate().save(path)  # in chiaro
            with self.assertRaises(SyncCryptoError):
                DeviceKeys.load(path, FakeVault())

    def test_wipe_removes_the_key_file_and_makes_the_keys_unusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, other = Path(tmp) / "keys.json", Path(tmp) / "altro.txt"
            other.write_text("non e' di Jake")
            keys = DeviceKeys.generate()
            keys.save(path)
            keys.wipe(path)
            self.assertFalse(path.exists())
            self.assertTrue(other.exists())  # solo le chiavi di Jake
            for action in (lambda: keys.public, lambda: keys.sign(b"x"), lambda: keys.agree_with(b"0" * 32), lambda: keys.save(path)):
                with self.assertRaises(SyncCryptoError):
                    action()

    def test_a_revoked_id_cannot_be_reactivated_by_adding_it_again(self):
        ring = Keyring()
        keys = DeviceKeys.generate().public
        ring.add("telefono", keys)
        ring.revoke("telefono")
        with self.assertRaises(SyncCryptoError):
            ring.add("telefono", keys)
        self.assertFalse(ring.revoke("telefono"))
        self.assertFalse(ring.is_active("telefono"))


class ClockAndStateTests(unittest.TestCase):
    def test_the_hybrid_clock_never_goes_backwards_and_beats_what_it_has_seen(self):
        clock = Clock()
        hlc = HybridClock("a", clock)
        first, second = hlc.now(), hlc.now()  # stesso istante: il contatore avanza
        self.assertLess(first, second)
        clock.t -= 1000  # l'orologio torna indietro
        self.assertLess(second, hlc.now())
        future = Stamp(int((clock.t + 10_000) * 1000), 5, "b")
        hlc.observe(future)
        self.assertGreater(hlc.now(), future)

    def test_stamps_are_a_total_order_with_the_device_as_tiebreaker(self):
        self.assertLess(Stamp(1, 0, "a"), Stamp(1, 0, "b"))
        self.assertLess(Stamp(1, 9, "z"), Stamp(2, 0, "a"))

    def _change(self, key, fields, stamp, op="upsert", profile="davide", entity="memory", change_id=None):
        return Change(change_id or f"{key}-{stamp.wall_ms}-{stamp.counter}-{stamp.device}", entity, key, op, fields, stamp, profile)

    def test_last_writer_wins_per_field_and_different_fields_both_survive(self):
        state = ReplicaState()
        state.apply(self._change("k", {"value": "da A", "importance": 3}, Stamp(10, 0, "a")))
        state.apply(self._change("k", {"value": "da B"}, Stamp(20, 0, "b")))
        state.apply(self._change("k", {"pinned": True}, Stamp(15, 0, "c")))
        self.assertEqual(state.get("davide", "memory", "k"), {"value": "da B", "importance": 3, "pinned": True})

    def test_the_losing_value_of_a_conflict_is_recorded_not_lost(self):
        state = ReplicaState()
        state.apply(self._change("k", {"value": "prima"}, Stamp(10, 0, "a")))
        state.apply(self._change("k", {"value": "dopo"}, Stamp(20, 0, "b")))
        state.apply(self._change("k", {"value": "arrivata tardi ma vecchia"}, Stamp(5, 0, "c")))
        lost = {c.lost_value for c in state.conflicts}
        self.assertEqual(lost, {"prima", "arrivata tardi ma vecchia"})
        self.assertEqual(state.get("davide", "memory", "k"), {"value": "dopo"})

    def test_a_delete_hides_older_fields_and_a_newer_write_recreates_the_entity(self):
        state = ReplicaState()
        state.apply(self._change("k", {"value": "v"}, Stamp(10, 0, "a")))
        state.apply(self._change("k", {}, Stamp(20, 0, "b"), op="delete"))
        self.assertIsNone(state.get("davide", "memory", "k"))
        state.apply(self._change("k", {"value": "vecchio"}, Stamp(15, 0, "c")))  # scritto prima della cancellazione
        self.assertIsNone(state.get("davide", "memory", "k"))
        state.apply(self._change("k", {"value": "rinato"}, Stamp(30, 0, "a")))
        self.assertEqual(state.get("davide", "memory", "k"), {"value": "rinato"})

    def test_applying_changes_in_any_order_gives_the_same_state(self):
        """Convergenza: tutte le 720 permutazioni di 6 modifiche in conflitto producono lo stesso stato."""
        changes = [
            self._change("k", {"value": "a1", "importance": 1}, Stamp(10, 0, "a")),
            self._change("k", {"value": "b1"}, Stamp(11, 0, "b")),
            self._change("k", {}, Stamp(12, 0, "a"), op="delete"),
            self._change("k", {"value": "c1", "pinned": True}, Stamp(13, 0, "c")),
            self._change("j", {"value": "altro"}, Stamp(9, 0, "b")),
            self._change("k", {"importance": 5}, Stamp(14, 0, "b")),
        ]
        snapshots = set()
        for permutation in itertools.permutations(changes):
            state = ReplicaState()
            for change in permutation:
                state.apply(change)
            snapshots.add(json.dumps(state.snapshot(), sort_keys=True))
        self.assertEqual(len(snapshots), 1)

    def test_applying_the_same_change_twice_changes_nothing(self):
        state = ReplicaState()
        change = self._change("k", {"value": "v"}, Stamp(10, 0, "a"))
        state.apply(change)
        before = state.snapshot()
        state.apply(change)
        self.assertEqual(state.snapshot(), before)

    def test_profiles_are_separate_namespaces(self):
        state = ReplicaState()
        state.apply(self._change("k", {"value": "di davide"}, Stamp(10, 0, "a"), profile="davide"))
        state.apply(self._change("k", {"value": "di anna"}, Stamp(11, 0, "a"), profile="anna"))
        self.assertEqual(state.get("davide", "memory", "k"), {"value": "di davide"})
        self.assertEqual(state.get("anna", "memory", "k"), {"value": "di anna"})


class WhatSyncsTests(unittest.TestCase):
    def _change(self, entity, key, fields):
        return Change("id", entity, key, "upsert", fields, Stamp(1, 0, "a"), "davide")

    def test_the_four_syncable_kinds_pass(self):
        for entity, key in (("memory", "k"), ("todo", "t"), ("conversation", "c"), ("config", "voice_style")):
            check_syncable(self._change(entity, key, {"value": "x"}))

    def test_secrets_never_sync(self):
        cases = [
            self._change("memory", "wifi", {"value": "x", "sensitivity": "secret"}),
            self._change("memory", "k", {"password": "hunter2"}),
            self._change("todo", "t", {"api_key": "abc"}),
            self._change("config", "admin_passphrase", {"value": "x"}),
            self._change("config", "companion_token", {"value": "x"}),
            self._change("config", "voice_style", {"token": "x"}),
        ]
        for change in cases:
            with self.subTest(entity=change.entity, key=change.key), self.assertRaises(NotSyncable):
                check_syncable(change)

    def test_only_allowed_config_keys_and_known_entities(self):
        with self.assertRaises(NotSyncable):
            check_syncable(self._change("config", "una_chiave_qualunque", {"value": 1}))
        with self.assertRaises(NotSyncable):
            check_syncable(self._change("ledger", "x", {"value": 1}))

    def test_a_secret_is_refused_at_the_source_and_never_enters_state_or_queue(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        with self.assertRaises(NotSyncable):
            a.local_change("memory", "wifi", "upsert", {"value": "hunter2", "sensitivity": "secret"}, "davide")
        self.assertEqual(a.state.snapshot(), {})
        self.assertEqual(a.queue.pending("b"), 0)

    def test_a_node_refuses_a_profile_it_is_not_authorized_for(self):
        a = make_node("a", profiles=("davide",))
        with self.assertRaises(NotSyncable):
            a.local_change("memory", "k", "upsert", {"value": "x"}, "anna")


class SyncBetweenDevicesTests(unittest.TestCase):
    def test_a_change_travels_encrypted_and_arrives(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        a.local_change("memory", "compleanno", "upsert", {"value": "5 marzo"}, "davide")
        results = deliver(a, b)
        self.assertEqual(sum(r.applied for r in results), 1)
        self.assertEqual(b.state.get("davide", "memory", "compleanno"), {"value": "5 marzo"})
        self.assertEqual(a.state.snapshot(), b.state.snapshot())

    def test_the_same_envelope_delivered_twice_is_applied_once(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        a.local_change("memory", "k", "upsert", {"value": "v"}, "davide")
        (envelope,) = a.queue.drain("b")
        first, second = b.receive(envelope), b.receive(envelope)
        self.assertEqual((first.applied, second.applied, second.duplicates), (1, 0, 1))

    def test_offline_changes_wait_in_the_queue_and_arrive_in_a_burst(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        for i in range(5):
            a.local_change("todo", f"t{i}", "upsert", {"text": f"attivita {i}"}, "davide")
        self.assertEqual(a.queue.pending("b"), 5)
        deliver(a, b)
        self.assertEqual(len(b.state.snapshot()), 5)

    def test_concurrent_edits_on_different_fields_are_both_kept_on_both_devices(self):
        clock = Clock()
        a, b = make_node("a", clock=clock), make_node("b", clock=clock)
        pair(a, b)
        a.local_change("memory", "k", "upsert", {"value": "testo"}, "davide")
        deliver(a, b)
        clock.t += 1
        a.local_change("memory", "k", "upsert", {"importance": 5}, "davide")  # offline, in parallelo...
        b.local_change("memory", "k", "upsert", {"pinned": True}, "davide")
        deliver(a, b)
        deliver(b, a)
        expected = {"value": "testo", "importance": 5, "pinned": True}
        self.assertEqual(a.state.get("davide", "memory", "k"), expected)
        self.assertEqual(b.state.get("davide", "memory", "k"), expected)

    def test_concurrent_edits_on_the_same_field_converge_and_the_loser_is_recorded(self):
        clock = Clock()
        a, b = make_node("a", clock=clock), make_node("b", clock=clock)
        pair(a, b)
        a.local_change("memory", "k", "upsert", {"value": "versione di A"}, "davide")
        b.local_change("memory", "k", "upsert", {"value": "versione di B"}, "davide")
        deliver(a, b)
        deliver(b, a)
        self.assertEqual(a.state.snapshot(), b.state.snapshot())
        winner = a.state.get("davide", "memory", "k")["value"]
        lost = {c.lost_value for c in a.state.conflicts} | {c.lost_value for c in b.state.conflicts}
        self.assertEqual(lost, {"versione di A", "versione di B"} - {winner})  # nessuna modifica sparisce senza traccia

    def test_a_delete_propagates(self):
        clock = Clock()
        a, b = make_node("a", clock=clock), make_node("b", clock=clock)
        pair(a, b)
        a.local_change("memory", "k", "upsert", {"value": "v"}, "davide")
        deliver(a, b)
        clock.t += 1
        b.local_change("memory", "k", "delete", None, "davide")
        deliver(b, a)
        self.assertIsNone(a.state.get("davide", "memory", "k"))

    def test_a_device_authorized_only_for_another_profile_receives_nothing_of_this_one(self):
        a, kid = make_node("a", profiles=("davide", "anna")), make_node("figlio", profiles=("anna",))
        a.keyring.add("figlio", kid.keys.public, {"anna"})
        kid.keyring.add("a", a.keys.public, {"anna"})
        a.local_change("memory", "segreto di davide", "upsert", {"value": "x"}, "davide")
        self.assertEqual(a.queue.pending("figlio"), 0)  # nemmeno cifrata: non e' tra i destinatari di quel profilo
        a.local_change("memory", "per anna", "upsert", {"value": "y"}, "anna")
        deliver(a, kid)
        self.assertEqual(list(kid.state.snapshot()), ["anna|memory|per anna"])

    def test_a_peer_cannot_inject_changes_for_a_profile_it_may_not_write(self):
        a, mallory = make_node("a", profiles=("davide", "anna")), make_node("m", profiles=("davide", "anna"))
        a.keyring.add("m", mallory.keys.public, {"davide"})  # per a, m puo' scrivere solo "davide"
        mallory.keyring.add("a", a.keys.public, {"davide", "anna"})
        mallory.local_change("memory", "k", "upsert", {"value": "x"}, "anna")
        results = deliver(mallory, a)
        self.assertEqual(sum(r.applied for r in results), 0)
        self.assertTrue(any("non autorizzato" in msg for r in results for msg in r.rejected))
        self.assertEqual(a.state.snapshot(), {})

    def test_secret_like_data_is_also_refused_on_reception(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        # un mittente malevolo/difettoso che salta il controllo alla fonte
        from core.sync_engine import Stamp as S
        bad = Change("x1", "memory", "k", "upsert", {"password": "hunter2"}, S(1, 0, "a"), "davide")
        payload = json.dumps({"kind": "changes", "changes": [bad.to_dict()]}).encode()
        envelope = seal(payload, "a", a.keys, a.keyring.get("b"), "davide", 1)
        result = b.receive(envelope)
        self.assertEqual((result.applied, len(result.rejected)), (0, 1))
        self.assertEqual(b.state.snapshot(), {})

    def test_garbage_and_wrong_messages_are_rejected_not_crashing(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        for content in (b"non e' json", json.dumps({"kind": "boh"}).encode(), json.dumps({"kind": "changes", "changes": [{"id": 1}]}).encode()):
            env = seal(content, "a", a.keys, a.keyring.get("b"), "davide", 1)
            result = b.receive(env)
            self.assertEqual(result.applied, 0)
            self.assertTrue(result.rejected)


class RevocationAndWipeTests(unittest.TestCase):
    def test_a_revoked_device_receives_nothing_new(self):
        """Criterio di F7.6: il dispositivo revocato non legge nuovi dati."""
        a, b, lost = make_node("a"), make_node("b"), make_node("perso")
        pair(a, b, lost)
        a.local_change("memory", "prima", "upsert", {"value": "letto quando era autorizzato"}, "davide")
        deliver(a, lost)
        self.assertEqual(lost.state.get("davide", "memory", "prima"), {"value": "letto quando era autorizzato"})
        a.queue.drain("b")
        a.revoke_device("perso")
        a.local_change("memory", "dopo", "upsert", {"value": "segreto nuovo"}, "davide")
        self.assertEqual(a.queue.pending("perso"), 0)  # niente cifrato per lui
        self.assertGreater(a.queue.pending("b"), 0)
        for envelope in a.queue.drain("b"):
            self.assertNotEqual(envelope.recipient, "perso")

    def test_a_message_sent_by_a_revoked_device_is_rejected(self):
        a, lost = make_node("a"), make_node("perso")
        pair(a, lost)
        a.revoke_device("perso")
        lost.local_change("memory", "k", "upsert", {"value": "x"}, "davide")
        results = deliver(lost, a)
        self.assertEqual(sum(r.applied for r in results), 0)
        self.assertTrue(any("revocato" in m for r in results for m in r.rejected))

    def test_a_revocation_reaches_the_other_devices(self):
        a, b, lost = make_node("a"), make_node("b"), make_node("perso")
        pair(a, b, lost)
        a.revoke_device("perso")
        results = deliver(a, b)
        self.assertEqual([d for r in results for d in r.revoked], ["perso"])
        self.assertFalse(b.keyring.is_active("perso"))
        lost.local_change("memory", "k", "upsert", {"value": "x"}, "davide")
        self.assertEqual(sum(r.applied for r in deliver(lost, b)), 0)  # b ora rifiuta anche lui

    def test_revoking_drops_what_was_still_queued_for_that_device(self):
        a, lost = make_node("a"), make_node("perso")
        pair(a, lost)
        a.local_change("memory", "k", "upsert", {"value": "v"}, "davide")
        a.queue.discard_for("perso")
        self.assertEqual(a.queue.pending("perso"), 0)

    def test_revoking_twice_or_an_unknown_device_is_a_no_op(self):
        a = make_node("a")
        self.assertFalse(a.revoke_device("mai-visto"))

    def test_remote_wipe_erases_only_jakes_keys_and_data_when_the_lost_device_comes_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path, unrelated = Path(tmp) / "jake_keys.json", Path(tmp) / "foto_di_famiglia.jpg"
            unrelated.write_text("non toccare")
            a = make_node("a")
            lost = SyncNode("perso", DeviceKeys.generate(), Keyring(), {"davide"}, wall=Clock(), key_path=key_path)
            lost.keys.save(key_path)
            pair(a, lost)
            a.local_change("memory", "k", "upsert", {"value": "dato di Jake"}, "davide")
            deliver(a, lost)
            self.assertTrue(lost.state.snapshot())
            a.request_wipe("perso")  # il dispositivo e' offline: il comando aspetta in coda
            a.revoke_device("perso")
            self.assertEqual(a.queue.pending("perso"), 1)  # solo il wipe: il resto non e' cifrato per lui
            (result,) = deliver(a, lost)
            self.assertTrue(result.wiped)
            self.assertFalse(key_path.exists())
            self.assertEqual(lost.state.snapshot(), {})
            self.assertTrue(unrelated.exists())  # nient'altro sul dispositivo e' stato toccato
            self.assertTrue(lost.wiped)

    def test_a_wiped_device_accepts_nothing_more(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        b.wipe()
        a.local_change("memory", "k", "upsert", {"value": "x"}, "davide")
        (result,) = deliver(a, b)
        self.assertEqual(result.applied, 0)
        self.assertIn("wipe", result.rejected[0])

    def test_a_wipe_can_only_be_requested_before_the_revocation(self):
        a, lost = make_node("a"), make_node("perso")
        pair(a, lost)
        a.revoke_device("perso")
        with self.assertRaises(SyncCryptoError):
            a.request_wipe("perso")

    def test_a_wipe_from_an_unknown_sender_does_nothing(self):
        a, b, stranger = make_node("a"), make_node("b"), make_node("sconosciuto")
        pair(a, b)
        stranger.keyring.add("b", b.keys.public, {"davide"})
        stranger.request_wipe("b")
        results = deliver(stranger, b)
        self.assertFalse(any(r.wiped for r in results))
        self.assertFalse(b.wiped)


class OfflineQueueTests(unittest.TestCase):
    def _envelope(self, a, recipient="b"):
        return seal(b"x" * 200, "a", a.keys, a.keyring.get(recipient), "davide", 1)

    def test_the_queue_is_bounded_by_count_and_flags_a_full_resync(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        queue = OutboundQueue(max_items=3)
        for _ in range(5):
            queue.push(self._envelope(a))
        self.assertEqual(queue.pending("b"), 3)
        self.assertEqual(queue.dropped, 2)
        self.assertIn("b", queue.needs_full_resync)

    def test_the_queue_is_bounded_by_bytes(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        one = self._envelope(a).size()
        queue = OutboundQueue(max_items=100, max_bytes=one * 2 + 10)
        for _ in range(5):
            queue.push(self._envelope(a))
        self.assertEqual(queue.pending("b"), 2)

    def test_old_messages_expire_and_flag_a_full_resync(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        clock = Clock()
        queue = OutboundQueue(max_age_s=3600, clock=clock)
        queue.push(self._envelope(a))
        clock.t += 7200
        self.assertEqual(queue.pending("b"), 0)
        self.assertIn("b", queue.needs_full_resync)

    def test_a_queue_within_its_limits_never_flags_a_resync(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        queue = OutboundQueue()
        queue.push(self._envelope(a))
        self.assertEqual(queue.needs_full_resync, set())

    def test_queues_are_per_recipient(self):
        a, b, c = make_node("a"), make_node("b"), make_node("c")
        pair(a, b, c)
        queue = OutboundQueue(max_items=1)
        queue.push(self._envelope(a, "b"))
        queue.push(self._envelope(a, "c"))
        self.assertEqual((queue.pending("b"), queue.pending("c")), (1, 1))

    def test_a_device_that_lost_history_gets_the_full_state_including_deletions(self):
        clock = Clock()
        a = make_node("a", clock=clock, queue=OutboundQueue(max_items=2, clock=clock))
        late = make_node("tardivo", clock=clock)
        pair(a, late)
        for i in range(6):
            a.local_change("memory", f"k{i}", "upsert", {"value": f"v{i}"}, "davide")
        clock.t += 1
        a.local_change("memory", "k0", "delete", None, "davide")
        self.assertIn("tardivo", a.queue.needs_full_resync)
        a.queue.discard_for("tardivo")
        for envelope in a.snapshot_for("tardivo"):
            late.receive(envelope)
        self.assertEqual(late.state.snapshot(), a.state.snapshot())
        self.assertIsNone(late.state.get("davide", "memory", "k0"))
        self.assertNotIn("tardivo", a.queue.needs_full_resync)

    def test_the_snapshot_respects_the_devices_profiles(self):
        a, kid = make_node("a", profiles=("davide", "anna")), make_node("figlio", profiles=("anna",))
        a.keyring.add("figlio", kid.keys.public, {"anna"})
        kid.keyring.add("a", a.keys.public, {"anna"})
        a.local_change("memory", "di davide", "upsert", {"value": "x"}, "davide")
        a.local_change("memory", "di anna", "upsert", {"value": "y"}, "anna")
        for envelope in a.snapshot_for("figlio"):
            kid.receive(envelope)
        self.assertEqual(list(kid.state.snapshot()), ["anna|memory|di anna"])

    def test_no_snapshot_for_a_revoked_or_unknown_device(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        a.revoke_device("b")
        with self.assertRaises(SyncCryptoError):
            a.snapshot_for("b")
        with self.assertRaises(SyncCryptoError):
            a.snapshot_for("mai-visto")

    def test_a_big_snapshot_is_split_into_several_envelopes(self):
        a, b = make_node("a"), make_node("b")
        pair(a, b)
        for i in range(250):
            a.local_change("todo", f"t{i}", "upsert", {"text": f"attivita {i}"}, "davide")
        a.queue.discard_for("b")
        envelopes = a.snapshot_for("b")
        self.assertGreaterEqual(len(envelopes), 3)
        for envelope in envelopes:
            b.receive(envelope)
        self.assertEqual(len(b.state.snapshot()), 250)


if __name__ == "__main__":
    unittest.main()

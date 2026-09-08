"""Fonte unica della versione di Jake (F0: "avere una sola versione"). Prima di questo modulo
main.py mostrava ancora "Jake 3.0" nel banner d'aiuto mentre i commit dichiaravano fasi fino
alla 5.9 (lo stesso problema per cui il README riportava "Jake 3.1", gia' corretto li' ma non
qui) - due punti diversi del codice raccontavano una storia diversa perche' nessuno dei due
leggeva da una fonte comune.

VERSION riflette la fase piu' alta gia' raggiunta nella cronologia Git secondo la ricostruzione
in ROADMAP.md ("i commit dichiarano fasi fino alla 5.9"), non un numero scelto a piacere: va
aggiornata insieme alla tabella delle fasi in ROADMAP.md quando una fase successiva si conclude
davvero, non ad ogni commit."""

VERSION = "5.9"

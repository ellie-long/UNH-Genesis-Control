"""Readers for past cool-down records (LabVIEW ``data_record_*.csv``, ssRF
``blast_log_*.csv``) and the sample-and-hold timeline the emulators read from.

Columns are always found by header title through an era alias table, never by
position: the LabVIEW DAQ renamed and reordered columns several times between
2020 and 2026.  A channel whose column is absent in an era is *absent* -- the
simulator reports the instrument as not present rather than inventing data.
"""

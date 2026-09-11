"""Probe temporaneo per F0.2.6: verifica empirica che master rifiuti un merge con CI rossa.

Questo file esiste solo sul branch usabile-e-getta ``test/branch-protection-verify`` e viene
rimosso prima che il branch venga cancellato; non deve mai raggiungere ``master``.
"""
import unittest


class BranchProtectionProbeTests(unittest.TestCase):
    def test_deliberately_fails_to_redden_ci(self) -> None:
        self.fail("F0.2.6 probe: red CI expected on this throwaway branch")


if __name__ == "__main__":
    unittest.main()

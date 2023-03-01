#!/usr/bin/env python3

# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""A  juju charm for Grafana Agent on Kubernetes."""
import logging
import pathlib
import subprocess
from typing import Dict, Optional, Union

from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus, Relation, Unit

from grafana_agent import GrafanaAgentCharm

logger = logging.getLogger(__name__)


class GrafanaAgentError(Exception):
    """Custom exception type for Grafana Agent."""

    pass


class GrafanaAgentInstallError(GrafanaAgentError):
    """Custom exception type for install related errors."""

    pass


class GrafanaAgentServiceError(GrafanaAgentError):
    """Custom exception type for service related errors."""

    pass


class GrafanaAgentK8sCharm(GrafanaAgentCharm):
    """K8s version of the Grafana Agent charm."""

    def __init__(self, *args):
        super().__init__(*args)
        self._service = "grafana-agent.grafana-agent"
        self.framework.observe(self.on.install, self.on_install)
        self.framework.observe(self.on.start, self.on_start)
        self.framework.observe(self.on.stop, self.on_stop)
        self.framework.observe(self.on.remove, self.on_remove)

    def on_install(self, _) -> None:
        """Install the Grafana Agent snap."""
        # Check if Grafana Agent is installed
        self.unit.status = MaintenanceStatus("Installing grafana-agent snap")
        if not self._is_installed:
            subprocess.run(["sudo", "snap", "install", "grafana-agent"])
            if not self._is_installed:
                raise GrafanaAgentInstallError("Failed to install grafana-agent.")

    def on_start(self, _) -> None:
        """Start Grafana Agent."""
        # Ensure the config is up to date before we start to avoid racy relation
        # changes and starting with a "bare" config in ActiveStatus
        self._update_config(None)
        self.unit.status = MaintenanceStatus("Starting grafana-agent snap")
        start_process = subprocess.run(["sudo", "snap", "start", "--enable", self._service])
        if start_process.returncode != 0:
            raise GrafanaAgentServiceError("Failed to start grafana-agent")
        self.unit.status = ActiveStatus()

    def on_stop(self, _) -> None:
        """Stop Grafana Agent."""
        self.unit.status = MaintenanceStatus("Stopping grafana-agent snap")
        stop_process = subprocess.run(["sudo", "snap", "stop", "--disable", self._service])
        if stop_process.returncode != 0:
            raise GrafanaAgentServiceError("Failed to stop grafana-agent")

    def on_remove(self, _) -> None:
        """Uninstall the Grafana Agent snap."""
        self.unit.status = MaintenanceStatus("Uninstalling grafana-agent snap")
        subprocess.run(["sudo", "snap", "remove", "--purge", "grafana-agent"])
        if self._is_installed:
            raise GrafanaAgentInstallError("Failed to uninstall grafana-agent")

    @property
    def is_ready(self):
        """Checks if the charm is ready for configuration."""
        return self._is_installed and self.principal_unit

    def agent_version_output(self) -> str:
        """Runs `agent -version` and returns the output.

        Returns:
            Output of `agent -version`
        """
        return subprocess.run(["/bin/agent", "-version"], capture_output=True, text=True).stdout

    def read_file(self, filepath: Union[str, pathlib.Path]):
        """Read a file's contents.

        Returns:
            A string with the file's contents
        """
        with open(filepath) as f:
            return f.read()

    def write_file(self, path: Union[str, pathlib.Path], text: str) -> None:
        """Write text to a file.

        Args:
            path: file path to write to
            text: text to write to the file
        """
        with open(path, "w") as f:
            f.write(text)

    def restart(self) -> None:
        """Restart grafana agent."""
        subprocess.run(["sudo", "snap", "restart", self._service])

    @property
    def is_machine(self) -> bool:
        """Check if this is a machine charm."""
        return True

    @property
    def _is_installed(self) -> bool:
        """Check if the Grafana Agent snap is installed."""
        package_check = subprocess.run("snap list | grep grafana-agent", shell=True)
        return True if package_check.returncode == 0 else False

    @property
    def _principal_relation(self) -> Optional[Relation]:
        if self.model.relations.get("juju-info"):
            return self.model.relations["juju-info"][0]
        else:
            return None

    @property
    def principal_unit(self) -> Optional[Unit]:
        """Return the principal unit this charm is subordinated to."""
        relation = self._principal_relation
        if relation:
            if relation.units:
                # Here, we could have popped the set and put the unit back or
                # memoized the function, but in the interest of backwards compatibility
                # with older python versions and avoiding adding temporary state to
                # the charm instance, we choose this somewhat unsightly option.
                return next(iter(relation.units))
            else:
                return None
        else:
            return None

    @property
    def _principal_topology(
        self,
    ) -> Dict[str, str]:
        unit = self.principal_unit
        if unit:
            # Note we can't include juju_charm as that information is not available to us.
            return {
                "juju_model": self.model.name,
                "juju_model_uuid": self.model.uuid,
                "juju_application": unit.app.name,
                "juju_unit": unit.name,
            }
        else:
            return {}

    @property
    def _instance_name(self) -> str:
        """Return the instance name as interpolated topology values."""
        return "_".join([v for v in self._principal_topology.values()])

    @property
    def _principal_labels(self) -> Dict[str, str]:
        """Return a dict with labels from the topology of the principal charm."""
        return {
            # Dict ordering will give the appropriate result here
            "instance": self._instance_name,
            **self._principal_topology,
        }

    @property
    def _principal_relabeling_config(self) -> list:
        """Return a relabel config with labels from the topology of the principal charm."""
        topology_relabels = [
            {
                "source_labels": ["__address__"],
                "target_label": key,
                "replacement": value,
            }
            for key, value in self._principal_topology.items()
        ]

        return [
            {"target_label": "instance", "regex": "(.*)", "replacement": self._instance_name}
        ] + topology_relabels


if __name__ == "__main__":
    main(GrafanaAgentK8sCharm)

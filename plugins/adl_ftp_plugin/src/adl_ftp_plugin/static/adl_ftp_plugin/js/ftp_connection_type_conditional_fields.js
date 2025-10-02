(function () {
    'use strict';

    function updateConnectionTypeFields() {
        const connectionTypeInputs = document.querySelectorAll('input[name="connection_type"]');
        let selectedType = null;

        connectionTypeInputs.forEach(function (input) {
            if (input.checked) {
                selectedType = input.value;
            }
        });

        if (!selectedType) return;

        // Get the field panels
        const ftpSettingsPanel = document.getElementById("panel-ftpftps_settings-section")
        const sftpSettingsPanel = document.getElementById("panel-sftp_settings-section")


        if (selectedType === 'sftp') {
            // Hide FTP/FTPS settings
            if (ftpSettingsPanel) ftpSettingsPanel.style.display = 'none';
            // Show SFTP settings
            if (sftpSettingsPanel) sftpSettingsPanel.style.display = '';
        } else {
            // Show FTP/FTPS settings for 'ftp' or 'ftps'
            if (ftpSettingsPanel) ftpSettingsPanel.style.display = '';
            // Hide SFTP settings
            if (sftpSettingsPanel) sftpSettingsPanel.style.display = 'none';
        }
    }

    // Initialize on page load
    document.addEventListener('DOMContentLoaded', function () {
        updateConnectionTypeFields();

        // Add change listeners to connection_type radio buttons
        const connectionTypeInputs = document.querySelectorAll('input[name="connection_type"]');

        connectionTypeInputs.forEach(function (input) {
            input.addEventListener('change', updateConnectionTypeFields);
        });
    });
})();
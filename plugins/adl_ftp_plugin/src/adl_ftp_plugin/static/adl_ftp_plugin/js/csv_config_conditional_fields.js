(function () {
    'use strict';

    function updateFieldVisibility() {
        const datetimeMode = document.querySelector('input[name="datetime_mode"]:checked');


        if (!datetimeMode) return;

        const mode = datetimeMode.value;

        // Get the field panels
        const singleColumnPanel = document.getElementById("panel-single_column_mode_settings-section")
        const separateColumnsPanel = document.getElementById("panel-separate_columns_mode_settings-section");

        if (mode === 'single') {
            // Show single column fields
            if (singleColumnPanel) singleColumnPanel.style.display = '';
            // Hide separate columns fields
            if (separateColumnsPanel) separateColumnsPanel.style.display = 'none';
        } else if (mode === 'separate') {
            // Hide single column fields
            if (singleColumnPanel) singleColumnPanel.style.display = 'none';
            // Show separate columns fields
            if (separateColumnsPanel) separateColumnsPanel.style.display = '';
        }
    }

    // Initialize on page load
    document.addEventListener('DOMContentLoaded', function () {
        updateFieldVisibility();

        // Add change listeners to radio buttons
        const datetimeModeInputs = document.querySelectorAll('input[name="datetime_mode"]');
        datetimeModeInputs.forEach(function (input) {
            input.addEventListener('change', updateFieldVisibility);
        });
    });
})();
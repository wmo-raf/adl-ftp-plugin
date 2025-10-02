(function () {
    'use strict';


    function updateDecoderFields() {
        const decoderSelect = document.querySelector('select[name="decoder"]');

        if (!decoderSelect) return;

        const selectedDecoder = decoderSelect.value;

        // Get the CSV config field wrapper
        const csvConfigField = document.getElementById('panel-csv_config-section');

        if (selectedDecoder === 'standard_csv') {
            // Show CSV config field
            if (csvConfigField) {
                csvConfigField.style.display = '';
                // Add required indicator if not already present
                const label = csvConfigField.querySelector('label');
                if (label && !label.classList.contains('required')) {
                    label.classList.add('required');
                }
            }
        } else {
            // Hide CSV config field
            if (csvConfigField) {
                csvConfigField.style.display = 'none';
                // Remove required indicator
                const label = csvConfigField.querySelector('label');
                if (label) {
                    label.classList.remove('required');
                }
            }
        }
    }

    // Initialize on page load
    document.addEventListener('DOMContentLoaded', function () {
        updateDecoderFields();

        // Add change listener to decoder select
        const decoderSelect = document.querySelector('select[name="decoder"]');
        if (decoderSelect) {
            decoderSelect.addEventListener('change', updateDecoderFields);
        }
    });
})();
$(document).ready(function () {
    const $dirStructuredByDate = $('#id_dir_structured_by_date')

    const $dateGranularityInput = $('#id_date_granularity');
    const $dateGranularityInputWrapper = $dateGranularityInput.closest('.w-panel__wrapper');

    const $monthDirFormatInput = $('#id_month_dir_format');
    const $monthDirFormatInputWrapper = $monthDirFormatInput.closest('.w-panel__wrapper');

    // Initial check to set visibility based on the checkbox state
    showHideDirDateFields();

    // Event listener for checkbox change
    $dirStructuredByDate.on('change', function () {
        showHideDirDateFields();
    });

    function showHideDirDateFields() {
        if ($dirStructuredByDate.is(':checked')) {
            $dateGranularityInputWrapper.show();
            $monthDirFormatInputWrapper.show();
        } else {
            $dateGranularityInputWrapper.hide();
            $monthDirFormatInputWrapper.hide();
        }
    }


    const $filterFilesByDate = $('#id_filter_files_by_date')

    const $filenameDateFormatInput = $('#id_filename_date_format');
    const $filenameDateFormatInputWrapper = $filenameDateFormatInput.closest('.w-panel__wrapper');

    const $filenameDateTimezoneInput = $('#id_filename_date_timezone');
    const $filenameDateTimezoneInputWrapper = $filenameDateTimezoneInput.closest('.w-panel__wrapper');

    // Initial check to set visibility based on the checkbox state
    showHideFilterDateFields();

    // Event listener for checkbox change
    $filterFilesByDate.on('change', function () {
        showHideFilterDateFields();
    });

    function showHideFilterDateFields() {
        if ($filterFilesByDate.is(':checked')) {
            $filenameDateFormatInputWrapper.show();
            $filenameDateTimezoneInputWrapper.show();
        } else {
            $filenameDateFormatInputWrapper.hide();
            $filenameDateTimezoneInputWrapper.hide();
        }
    }
});
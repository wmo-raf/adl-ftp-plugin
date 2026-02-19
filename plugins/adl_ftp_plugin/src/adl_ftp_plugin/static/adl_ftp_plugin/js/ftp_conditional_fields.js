$(document).ready(function () {
    // Directory Structure
    const $dirStructuredByDate = $('#id_dir_structured_by_date');
    const $dateGranularityWrapper = $('#id_date_granularity').closest('.w-panel__wrapper');
    const $monthDirFormatWrapper = $('#id_month_dir_format').closest('.w-panel__wrapper');

    // Listing Strategy
    const $listingStrategy = $('#id_listing_strategy');

    // File Pattern field — not needed for Direct Fetch
    const $filePatternWrapper = $('#id_file_pattern').closest('.w-panel__wrapper');

    // Filter by Date section
    const $filterByDateSection = $('#panel-filter_by_date-section');

    // Direct Fetch section
    const $directFetchSection = $('#panel-direct_fetch-section');

    // Initial state
    showHideDirDateFields();
    showHideStrategyFields();

    // Listeners
    $dirStructuredByDate.on('change', showHideDirDateFields);
    $listingStrategy.on('change', showHideStrategyFields);

    function showHideDirDateFields() {
        const checked = $dirStructuredByDate.is(':checked');
        $dateGranularityWrapper.toggle(checked);
        $monthDirFormatWrapper.toggle(checked);
    }

    function showHideStrategyFields() {
        const strategy = $listingStrategy.val();
        const isDirectFetch = strategy === 'direct_fetch';

        $filePatternWrapper.toggle(!isDirectFetch);
        $filterByDateSection.toggle(strategy === 'filter_by_date');
        $directFetchSection.toggle(isDirectFetch);
    }
});
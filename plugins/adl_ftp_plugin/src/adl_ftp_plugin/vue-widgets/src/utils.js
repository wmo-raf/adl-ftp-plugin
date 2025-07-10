export const fetchDirectories = async (baseUrl, connectionId, {rootRequest = false, remotePath = null} = {}) => {
    try {
        const params = new URLSearchParams({connection_id: connectionId});

        if (remotePath) params.append('remote_path', remotePath);
        if (rootRequest) params.append('root_request', 'true');

        const url = `${baseUrl}?${params.toString()}`;
        const response = await fetch(url);

        if (!response.ok) {
            const {error} = await response.json();
            throw new Error(error || 'Failed to fetch directories');
        }

        const data = await response.json();
        return data.directories || [];
    } catch (err) {
        console.error('fetchDirectories error:', err.message);
        throw err;
    }
};
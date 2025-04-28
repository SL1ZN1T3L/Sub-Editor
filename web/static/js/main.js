document.addEventListener('DOMContentLoaded', function() {
    const uploadForm = document.getElementById('upload-form');
    const fileInput = document.getElementById('file-input');
    const createInfiniteStorageBtn = document.getElementById('createInfiniteStorageBtn');

    // --- CSRF Token Handling ---
    let csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');

    async function refreshCsrfToken() {
        try {
            const response = await fetch(window.location.href); // Fetch current page to get new token
            if (response.ok) {
                const html = await response.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');
                const newToken = doc.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
                if (newToken) {
                    csrfToken = newToken;
                    document.querySelector('meta[name="csrf-token"]')?.setAttribute('content', newToken);
                    console.log('CSRF token refreshed');
                    return true;
                }
            }
            console.error('Failed to refresh CSRF token: Response not OK or token not found');
            return false;
        } catch (error) {
            console.error('Error refreshing CSRF token:', error);
            return false;
        }
    }

    // Function to show toast notifications (assuming it exists from temp_storage.html context)
    function showToast(message, type = 'info') {
        // Implement or reuse your toast notification logic here
        console.log(`Toast [${type}]: ${message}`);
        alert(`[${type}] ${message}`); // Simple alert fallback
    }

    // --- Upload Form Handling ---
    if (uploadForm) {
        uploadForm.addEventListener('submit', async function(e) {
            e.preventDefault();

            const files = fileInput.files;
            if (files.length === 0) {
                showToast('Пожалуйста, выберите файлы для загрузки', 'warning');
                return;
            }

            const formData = new FormData();
            for (let i = 0; i < files.length; i++) {
                formData.append('file', files[i]);
            }

            // Determine the correct upload URL based on context (user_space vs temp_storage)
            const uploadUrl = window.linkId ? `/${window.linkId}/upload` : `/user/${window.userId}/upload`; // Assuming userId is available globally or via data attribute for user_space

            try {
                const response = await fetch(uploadUrl, { // Use dynamic URL
                    method: 'POST',
                    body: formData,
                    headers: {
                        'X-CSRF-Token': csrfToken // Include CSRF token
                    }
                });

                const result = await response.json();

                if (response.ok && result.success) {
                    showToast('Файлы успешно загружены', 'success');
                    location.reload();
                } else {
                    if (response.status === 403 && result.error?.includes('CSRF')) {
                        showToast('Сессия истекла, обновляем...', 'info');
                        if (await refreshCsrfToken()) {
                            showToast('Сессия обновлена, попробуйте загрузить снова.', 'info');
                        } else {
                            showToast('Не удалось обновить сессию. Обновите страницу.', 'error');
                        }
                    } else {
                        showToast(result.error || 'Ошибка при загрузке файлов', 'error');
                    }
                }
            } catch (error) {
                console.error('Ошибка:', error);
                showToast('Произошла ошибка при загрузке файлов', 'error');
            }
        });
    }

    // --- Create Infinite Storage Button Handling ---
    if (createInfiniteStorageBtn) {
        createInfiniteStorageBtn.addEventListener('click', async function() {
            const userId = this.getAttribute('data-user-id');
            if (!userId) {
                showToast('Не удалось определить ID пользователя', 'error');
                return;
            }

            if (!confirm(`Вы уверены, что хотите сделать хранилище пользователя ${userId} бесконечным? Это действие необратимо через интерфейс.`)) {
                return;
            }

            try {
                const response = await fetch('/create_infinite_storage', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrfToken // Include CSRF token
                    },
                    body: JSON.stringify({ user_id: userId })
                });

                const result = await response.json();

                if (response.ok && result.success) {
                    showToast(result.message || 'Хранилище успешно сделано бесконечным', 'success');
                    // Optionally disable the button or reload the page
                    this.disabled = true;
                    this.textContent = 'Хранилище бесконечно';
                    location.reload(); // Reload to reflect changes if needed
                } else {
                     if (response.status === 403 && result.error?.includes('CSRF')) {
                        showToast('Сессия истекла, обновляем...', 'info');
                        if (await refreshCsrfToken()) {
                            showToast('Сессия обновлена, попробуйте снова.', 'info');
                        } else {
                            showToast('Не удалось обновить сессию. Обновите страницу.', 'error');
                        }
                    } else if (response.status === 403) {
                         showToast(result.error || 'Доступ запрещен. У вас нет прав администратора.', 'error');
                    }
                     else {
                        showToast(result.error || 'Ошибка при создании бесконечного хранилища', 'error');
                    }
                }
            } catch (error) {
                console.error('Ошибка:', error);
                showToast('Произошла сетевая ошибка', 'error');
            }
        });
    }

});

// --- Delete File Function (Global Scope) ---
async function deleteFile(filename) {
    if (!confirm(`Вы уверены, что хотите удалить файл "${filename}"?`)) {
        return;
    }

    // Determine the correct delete URL
    const deleteUrl = window.linkId
        ? `/${window.linkId}/delete/${encodeURIComponent(filename)}`
        : `/user/${window.userId}/delete/${encodeURIComponent(filename)}`; // Assuming userId is available

    // Get CSRF token (it might have been refreshed)
    let currentCsrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');

    try {
        const response = await fetch(deleteUrl, {
            method: 'POST',
            headers: {
                'X-CSRF-Token': currentCsrfToken // Include CSRF token
            }
        });

        const result = await response.json();

        if (response.ok && result.success) {
            showToast('Файл успешно удален', 'success');
            location.reload();
        } else {
            if (response.status === 403 && result.error?.includes('CSRF')) {
                showToast('Сессия истекла, обновляем...', 'info');
                // Attempt to refresh token (assuming refreshCsrfToken is available globally or within scope)
                if (typeof refreshCsrfToken === 'function' && await refreshCsrfToken()) {
                     showToast('Сессия обновлена, попробуйте удалить снова.', 'info');
                 } else {
                     showToast('Не удалось обновить сессию. Обновите страницу.', 'error');
                 }
            } else {
                showToast(result.error || 'Ошибка при удалении файла', 'error');
            }
        }
    } catch (error) {
        console.error('Ошибка:', error);
        showToast('Произошла ошибка при удалении файла', 'error');
    }
}

// Helper function to show toast (if not already defined globally)
if (typeof showToast === 'undefined') {
    function showToast(message, type = 'info') {
        console.log(`Toast [${type}]: ${message}`);
        alert(`[${type}] ${message}`); // Simple fallback
    }
}

// Make userId available globally if needed (adjust based on how user_id is passed from Flask)
// Example: Assuming user_id is passed to the template and set here
// const userMeta = document.querySelector('meta[name="user-id"]');
// window.userId = userMeta ? userMeta.getAttribute('content') : null;
// Or get it from the button if it's the only place needed
// window.userId = document.getElementById('createInfiniteStorageBtn')?.getAttribute('data-user-id');
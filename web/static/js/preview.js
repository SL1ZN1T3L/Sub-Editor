/**
 * File preview functionality for the temporary storage application
 */

class FilePreviewManager {
    constructor(linkId) {
        this.linkId = linkId;
        this.previewModal = document.getElementById('previewModal');
        this.previewContainer = document.getElementById('previewContainer');
        this.previewFilename = document.getElementById('previewFilename');
        this.previewableFiles = [];
        this.currentIndex = 0;
        
        // Initialize event listeners
        this.initEventListeners();
    }
    
    // Initialize event listeners
    initEventListeners() {
        // Close button
        document.getElementById('previewClose').addEventListener('click', () => {
            this.closePreview();
        });
        
        // Navigation buttons
        document.getElementById('previewPrev').addEventListener('click', () => {
            this.showPrevious();
        });
        
        document.getElementById('previewNext').addEventListener('click', () => {
            this.showNext();
        });
        
        // Keyboard navigation
        document.addEventListener('keydown', (e) => {
            if (!this.previewModal.classList.contains('active')) return;
            
            switch(e.key) {
                case 'Escape':
                    this.closePreview();
                    break;
                case 'ArrowLeft':
                    this.showPrevious();
                    break;
                case 'ArrowRight':
                    this.showNext();
                    break;
            }
        });
        
        // Close on click outside
        this.previewModal.addEventListener('click', (e) => {
            if (e.target === this.previewModal) {
                this.closePreview();
            }
        });
    }
    
    // Initialize preview buttons for all files
    initPreviewButtons() {
        // Add preview buttons to file names
        document.querySelectorAll('.file-item').forEach((row) => {
            const filenameElement = row.querySelector('.file-name');
            if (!filenameElement) return;
            
            const filename = filenameElement.textContent.trim();
            const ext = filename.split('.').pop().toLowerCase();
            
            if (this.isPreviewableFile(ext)) {
                // Create preview button if it doesn't exist yet
                if (!filenameElement.querySelector('.file-preview-button')) {
                    const previewBtn = document.createElement('button');
                    previewBtn.className = 'file-preview-button';
                    previewBtn.title = 'Предпросмотр';
                    previewBtn.innerHTML = '<i class="fas fa-eye"></i>';
                    previewBtn.dataset.filename = filename;
                    
                    previewBtn.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        this.showPreview(filename);
                    });
                    
                    filenameElement.appendChild(previewBtn);
                }
            }
        });
        
        // Add preview buttons to action column
        document.querySelectorAll('.file-item').forEach((row) => {
            const actionsCell = row.querySelector('.file-actions');
            const filenameElement = row.querySelector('.file-name');
            if (!actionsCell || !filenameElement) return;
            
            const filename = filenameElement.textContent.trim();
            const ext = filename.split('.').pop().toLowerCase();
            
            if (this.isPreviewableFile(ext)) {
                // Create action button if it doesn't exist yet
                if (!actionsCell.querySelector('.btn-preview')) {
                    const previewBtn = document.createElement('button');
                    previewBtn.className = 'btn btn-preview';
                    previewBtn.title = 'Предпросмотр';
                    previewBtn.innerHTML = '<i class="fas fa-eye"></i>';
                    previewBtn.dataset.filename = filename;
                    
                    previewBtn.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        this.showPreview(filename);
                    });
                    
                    // Insert before download button
                    const downloadButton = actionsCell.querySelector('.btn-download');
                    if (downloadButton) {
                        actionsCell.insertBefore(previewBtn, downloadButton);
                    } else {
                        actionsCell.appendChild(previewBtn);
                    }
                }
            }
        });
        
        // Update the list of previewable files
        this.updatePreviewableFilesList();
    }
    
    // Update the list of previewable files
    updatePreviewableFilesList() {
        this.previewableFiles = Array.from(document.querySelectorAll('.file-preview-button'))
            .map(btn => btn.dataset.filename);
    }
    
    // Check if file type is previewable
    isPreviewableFile(extension) {
        const previewableTypes = [
            'jpg', 'jpeg', 'png', 'gif', 'svg', 'webp', // Images
            'pdf', // PDF documents
            'txt', 'md', 'csv', 'json', 'xml', 'html', 'css', 'js' // Text files
        ];
        
        return previewableTypes.includes(extension.toLowerCase());
    }
    
    // Show preview for a specific file
    showPreview(filename) {
        // Update the list of previewable files
        this.updatePreviewableFilesList();
        
        // Find the index of the current file
        this.currentIndex = this.previewableFiles.indexOf(filename);
        
        if (this.currentIndex === -1) {
            console.error('File not found in previewable files list');
            return;
        }
        
        this.updatePreviewContent();
        this.previewModal.classList.add('active');
    }
    
    // Update the preview content based on current index
    updatePreviewContent() {
        const filename = this.previewableFiles[this.currentIndex];
        
        // Show loading state
        this.previewContainer.innerHTML = '<div class="preview-loading"><i class="fas fa-spinner fa-spin"></i> Загрузка...</div>';
        this.previewFilename.textContent = filename;
        
        // Get file extension
        const ext = filename.split('.').pop().toLowerCase();
        
        // Generate download URL
        const fileUrl = `/${this.linkId}/download/${encodeURIComponent(filename)}`;
        
        // Show appropriate preview based on file type
        if (['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp'].includes(ext)) {
            // Image preview
            const img = document.createElement('img');
            img.className = 'preview-image';
            img.alt = filename;
            
            // Set loading handler
            img.onload = () => {
                this.previewFilename.textContent = `${filename} (${img.naturalWidth}x${img.naturalHeight})`;
            };
            
            img.onerror = () => {
                this.showPreviewError();
            };
            
            img.src = fileUrl;
            this.previewContainer.innerHTML = '';
            this.previewContainer.appendChild(img);
        } 
        else if (ext === 'pdf') {
            // PDF preview
            const iframe = document.createElement('iframe');
            iframe.className = 'preview-iframe';
            iframe.src = fileUrl;
            iframe.title = filename;
            
            this.previewContainer.innerHTML = '';
            this.previewContainer.appendChild(iframe);
        }
        else if (['txt', 'md', 'csv', 'json', 'xml', 'html', 'css', 'js'].includes(ext)) {
            // Text file preview - fetch and display content
            fetch(fileUrl)
                .then(response => {
                    if (!response.ok) {
                        throw new Error('Network response was not ok');
                    }
                    return response.text();
                })
                .then(text => {
                    const pre = document.createElement('pre');
                    pre.className = 'preview-text';
                    pre.textContent = text;
                    
                    this.previewContainer.innerHTML = '';
                    this.previewContainer.appendChild(pre);
                })
                .catch(error => {
                    console.error('Error fetching text file:', error);
                    this.showPreviewError();
                });
        }
        else {
            // Unsupported file type
            this.showPreviewError();
        }
        
        // Update navigation controls visibility
        document.querySelector('.preview-controls').style.display = 
            this.previewableFiles.length > 1 ? 'flex' : 'none';
    }
    
    // Show error state in preview
    showPreviewError() {
        const filename = this.previewableFiles[this.currentIndex];
        
        this.previewContainer.innerHTML = `
            <div class="preview-error">
                <i class="fas fa-exclamation-triangle"></i>
                <p>Не удалось показать предпросмотр для этого типа файлов</p>
                <a href="/${this.linkId}/download/${encodeURIComponent(filename)}" 
                   class="btn btn-primary" target="_blank">
                   Скачать файл
                </a>
            </div>
        `;
    }
    
    // Show previous file
    showPrevious() {
        if (this.previewableFiles.length <= 1) return;
        
        this.currentIndex--;
        if (this.currentIndex < 0) {
            this.currentIndex = this.previewableFiles.length - 1;
        }
        
        this.updatePreviewContent();
    }
    
    // Show next file
    showNext() {
        if (this.previewableFiles.length <= 1) return;
        
        this.currentIndex++;
        if (this.currentIndex >= this.previewableFiles.length) {
            this.currentIndex = 0;
        }
        
        this.updatePreviewContent();
    }
    
    // Close preview modal
    closePreview() {
        this.previewModal.classList.remove('active');
    }
}

// Initialize the preview functionality when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    // Create preview manager with current link ID
    window.previewManager = new FilePreviewManager(linkId);
    
    // Initialize preview buttons
    window.previewManager.initPreviewButtons();
});
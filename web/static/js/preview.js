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
        
        // Также добавляем обработчик событий для кнопок с onclick
        this.setupInlineEventHandlers();
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
        // Добавляем кнопки только в столбец действий, 
        // убираем добавление кнопок к имени файла
        document.querySelectorAll('.file-item').forEach((row) => {
            const actionsCell = row.querySelector('.file-actions');
            const filenameElement = row.querySelector('.file-name');
            if (!actionsCell || !filenameElement) return;
            
            const filename = filenameElement.textContent.trim();
            const ext = filename.split('.').pop().toLowerCase();
            
            if (this.isPreviewableFile(ext)) {
                // Проверяем, нет ли уже кнопки предпросмотра
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
                    
                    // Вставляем кнопку в начало контейнера с действиями
                    if (actionsCell.firstChild) {
                        actionsCell.insertBefore(previewBtn, actionsCell.firstChild);
                    } else {
                        actionsCell.appendChild(previewBtn);
                    }
                }
            }
        });
        
        // Update the list of previewable files
        this.updatePreviewableFilesList();
    }
    
    // Добавляем новый метод для обработки inline обработчиков событий
    setupInlineEventHandlers() {
        // Находим все существующие кнопки предпросмотра, которые могли быть добавлены через шаблон
        document.querySelectorAll('.btn-preview').forEach(button => {
            // Удаляем существующий обработчик onclick, если он есть
            if (button.hasAttribute('onclick')) {
                const filename = button.getAttribute('data-filename');
                if (filename) {
                    // Сохраняем filename в dataset, если его там еще нет
                    button.dataset.filename = filename;
                }
                button.removeAttribute('onclick');
            }
            
            // Добавляем обработчик клика
            button.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const filename = button.dataset.filename;
                if (filename) {
                    this.showPreview(filename);
                } else {
                    console.error('Кнопке предпросмотра не задан атрибут data-filename');
                }
            });
        });
    }
    
    // Update the list of previewable files
    updatePreviewableFilesList() {
        this.previewableFiles = Array.from(document.querySelectorAll('.btn-preview'))
            .map(btn => btn.dataset.filename)
            .filter(filename => filename); // Фильтруем undefined и пустые строки
    }
    
    // Check if file type is previewable
    isPreviewableFile(extension) {
        const previewableTypes = [
            // Изображения
            'jpg', 'jpeg', 'png', 'gif', 'svg', 'webp', 'bmp', 'ico', 'tiff', 'tif',
            
            // PDF документы
            'pdf',
            
            // Microsoft Office документы
            'doc', 'docx', 'docm', 'dot', 'dotx', 'dotm',     // Word
            'xls', 'xlsx', 'xlsm', 'xlt', 'xltx', 'xltm',     // Excel
            'ppt', 'pptx', 'pptm', 'pot', 'potx', 'potm',     // PowerPoint
            'vsd', 'vsdx', 'vdw', 'vss', 'vssx',              // Visio
            'mdb', 'accdb', 'accde', 'accdt',                 // Access
            'rtf', 'odt', 'ods', 'odp',                       // Другие офисные форматы
            
            // Текстовые файлы и код
            'txt', 'md', 'csv', 'tsv', 'json', 'xml', 'html', 'htm', 'css', 'js',
            'py', 'java', 'c', 'cpp', 'h', 'hpp', 'cs', 'php', 'rb', 'go', 'rs', 'ts',
            'jsx', 'tsx', 'sql', 'yml', 'yaml', 'ini', 'conf', 'config', 'sh', 'bat', 'ps1',
            
            // Архивы (для показа структуры)
            'zip', 'rar', '7z', 'tar', 'gz', 'bz2',
            
            // Исходные файлы и документация
            'tex', 'bib', 'log', 'diff', 'patch'
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
        
        // Группируем расширения по типам для определения способа предпросмотра
        const imageTypes = ['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp', 'bmp', 'ico', 'tiff', 'tif'];
        const textTypes = ['txt', 'md', 'csv', 'tsv', 'json', 'xml', 'html', 'htm', 'css', 'js', 'py', 'java', 'c', 'cpp', 'h', 'hpp', 'cs', 'php', 'rb', 'go', 'rs', 'ts', 'jsx', 'tsx', 'sql', 'yml', 'yaml', 'ini', 'conf', 'config', 'sh', 'bat', 'ps1', 'tex', 'bib', 'log', 'diff', 'patch'];
        const pdfTypes = ['pdf'];
        const officeTypes = ['doc', 'docx', 'docm', 'dot', 'dotx', 'dotm', 'xls', 'xlsx', 'xlsm', 'xlt', 'xltx', 'xltm', 'ppt', 'pptx', 'pptm', 'pot', 'potx', 'potm', 'vsd', 'vsdx', 'vdw', 'vss', 'vssx', 'mdb', 'accdb', 'accde', 'accdt', 'rtf', 'odt', 'ods', 'odp'];
        const archiveTypes = ['zip', 'rar', '7z', 'tar', 'gz', 'bz2'];
        
        // Office документы по типам
        const wordTypes = ['doc', 'docx', 'docm', 'dot', 'dotx', 'dotm', 'rtf', 'odt'];
        const excelTypes = ['xls', 'xlsx', 'xlsm', 'xlt', 'xltx', 'xltm', 'ods'];
        const pptTypes = ['ppt', 'pptx', 'pptm', 'pot', 'potx', 'potm', 'odp'];
        
        // Show appropriate preview based on file type
        if (imageTypes.includes(ext)) {
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
        else if (pdfTypes.includes(ext)) {
            // Улучшенный предпросмотр PDF
            this.previewPdf(fileUrl, filename);
        }
        else if (textTypes.includes(ext)) {
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
        else if (officeTypes.includes(ext)) {
            // Обработка Office документов
            if (wordTypes.includes(ext)) {
                // Word документы
                this.previewWord(fileUrl, filename);
            } 
            else if (excelTypes.includes(ext)) {
                // Excel документы
                this.previewExcel(fileUrl, filename);
            } 
            else if (pptTypes.includes(ext)) {
                // PowerPoint документы
                this.previewPowerPoint(fileUrl, filename);
            } 
            else {
                // Другие офисные форматы показываем как раньше через ошибку с иконкой
                this.previewContainer.innerHTML = `
                    <div class="preview-error">
                        <i class="fas fa-file-${this.getOfficeIcon(ext)}"></i>
                        <p>Для предпросмотра данного формата документа требуется внешнее приложение</p>
                        <a href="${fileUrl}" class="btn btn-primary" target="_blank" download>
                            Скачать файл
                        </a>
                    </div>
                `;
            }
        }
        else if (archiveTypes.includes(ext)) {
            // Архивы: показываем иконку архива
            this.previewContainer.innerHTML = `
                <div class="preview-error">
                    <i class="fas fa-file-archive"></i>
                    <p>Для просмотра содержимого архива скачайте его</p>
                    <a href="${fileUrl}" class="btn btn-primary" target="_blank" download>
                        Скачать архив
                    </a>
                </div>
            `;
        }
        else {
            // Unsupported file type - попытка отобразить как изображение скриншота или другой файл
            // Скриншоты из Windows могут иметь расширение файла .png, но файлы в названии могут содержать { }
            if (filename.includes('{') && filename.includes('}')) {
                const img = document.createElement('img');
                img.className = 'preview-image';
                img.alt = filename;
                
                img.onload = () => {
                    this.previewFilename.textContent = `${filename} (${img.naturalWidth}x${img.naturalHeight})`;
                };
                
                img.onerror = () => {
                    this.showPreviewError();
                };
                
                img.src = fileUrl;
                this.previewContainer.innerHTML = '';
                this.previewContainer.appendChild(img);
            } else {
                this.showPreviewError();
            }
        }
        
        // Update navigation controls visibility
        document.querySelector('.preview-controls').style.display = 
            this.previewableFiles.length > 1 ? 'flex' : 'none';
    }
    
    // Специальный метод для правильного отображения PDF файлов
    previewPdf(fileUrl, filename) {
        // Создаем контейнер для PDF
        const pdfContainer = document.createElement('div');
        pdfContainer.className = 'preview-document preview-pdf';
        
        // Показываем загрузку
        pdfContainer.innerHTML = '<div class="preview-loading"><i class="fas fa-spinner fa-spin"></i> Загрузка PDF...</div>';
        this.previewContainer.innerHTML = '';
        this.previewContainer.appendChild(pdfContainer);

        // Используем PDF.js для отображения PDF файлов
        // Ссылка на библиотеку PDF.js
        const pdfJsUrl = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.4.120/pdf.min.js';
        
        // Функция для загрузки PDF.js, если она еще не загружена
        const loadPdfJs = () => {
            return new Promise((resolve, reject) => {
                if (window.pdfjsLib) {
                    resolve(window.pdfjsLib);
                    return;
                }
                
                const script = document.createElement('script');
                script.src = pdfJsUrl;
                script.onload = () => {
                    // Также загружаем worker для PDF.js
                    window.pdfjsLib.GlobalWorkerOptions.workerSrc = 
                        'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.4.120/pdf.worker.min.js';
                    resolve(window.pdfjsLib);
                };
                script.onerror = () => reject(new Error('Не удалось загрузить PDF.js'));
                document.head.appendChild(script);
            });
        };

        // Загружаем PDF с использованием PDF.js
        loadPdfJs()
            .then(pdfjs => {
                return pdfjs.getDocument(fileUrl).promise;
            })
            .then(pdfDoc => {
                // Создаем элементы для просмотра PDF
                pdfContainer.innerHTML = '';
                const canvas = document.createElement('canvas');
                canvas.className = 'pdf-canvas';
                pdfContainer.appendChild(canvas);
                
                // Создаем элементы навигации
                const navigation = document.createElement('div');
                navigation.className = 'pdf-navigation';
                navigation.innerHTML = `
                    <button class="pdf-prev-btn"><i class="fas fa-chevron-left"></i> Предыдущая</button>
                    <span class="pdf-page-info">Страница <span class="pdf-page-num">1</span> из ${pdfDoc.numPages}</span>
                    <button class="pdf-next-btn">Следующая <i class="fas fa-chevron-right"></i></button>
                    <a href="${fileUrl}" class="pdf-download-btn" download="${filename}">
                        <i class="fas fa-download"></i> Скачать
                    </a>
                `;
                pdfContainer.appendChild(navigation);
                
                const ctx = canvas.getContext('2d');
                let currentPage = 1;
                
                // Функция для рендеринга страницы PDF
                const renderPage = (pageNumber) => {
                    pdfDoc.getPage(pageNumber).then(page => {
                        const viewport = page.getViewport({scale: 1.5});
                        canvas.height = viewport.height;
                        canvas.width = viewport.width;
                        
                        const renderContext = {
                            canvasContext: ctx,
                            viewport: viewport
                        };
                        
                        page.render(renderContext);
                        
                        // Обновляем номер страницы
                        pdfContainer.querySelector('.pdf-page-num').textContent = pageNumber;
                    });
                };
                
                // Отрисовываем первую страницу
                renderPage(currentPage);
                
                // Добавляем обработчики для кнопок навигации
                pdfContainer.querySelector('.pdf-prev-btn').addEventListener('click', () => {
                    if (currentPage <= 1) return;
                    currentPage--;
                    renderPage(currentPage);
                });
                
                pdfContainer.querySelector('.pdf-next-btn').addEventListener('click', () => {
                    if (currentPage >= pdfDoc.numPages) return;
                    currentPage++;
                    renderPage(currentPage);
                });
            })
            .catch(error => {
                console.error('Ошибка при загрузке PDF:', error);
                this.fallbackPdfPreview(pdfContainer, fileUrl, filename);
            });
    }

    // Метод для предпросмотра Word документов
    previewWord(fileUrl, filename) {
        // Создаем контейнер для содержимого Word
        const wordContainer = document.createElement('div');
        wordContainer.className = 'preview-document preview-word';
        
        // Показываем информацию о документе с вариантами действий
        wordContainer.innerHTML = `
            <div class="office-preview-container">
                <div class="office-preview-icon">
                    <i class="fas fa-file-word fa-4x"></i>
                </div>
                <div class="office-preview-info">
                    <h3>Документ Microsoft Word</h3>
                    <p>Файл: ${filename}</p>
                    <div class="office-preview-actions">
                        <a href="${fileUrl}" class="btn btn-primary" download="${filename}">
                            <i class="fas fa-download"></i> Скачать документ
                        </a>
                        <a href="${fileUrl}?download=false" class="btn btn-secondary" target="_blank">
                            <i class="fas fa-external-link-alt"></i> Открыть в новой вкладке
                        </a>
                    </div>
                    <div class="office-preview-message">
                        <p>Для просмотра содержимого документа Word, пожалуйста, скачайте файл и откройте его в Microsoft Word или другом совместимом приложении.</p>
                    </div>
                </div>
            </div>
        `;
        
        this.previewContainer.innerHTML = '';
        this.previewContainer.appendChild(wordContainer);
    }

    // Метод для предпросмотра Excel документов
    previewExcel(fileUrl, filename) {
        // Создаем контейнер для содержимого Excel
        const excelContainer = document.createElement('div');
        excelContainer.className = 'preview-document preview-excel';
        
        // Показываем информацию о таблице с вариантами действий
        excelContainer.innerHTML = `
            <div class="office-preview-container">
                <div class="office-preview-icon">
                    <i class="fas fa-file-excel fa-4x"></i>
                </div>
                <div class="office-preview-info">
                    <h3>Таблица Microsoft Excel</h3>
                    <p>Файл: ${filename}</p>
                    <div class="office-preview-actions">
                        <a href="${fileUrl}" class="btn btn-primary" download="${filename}">
                            <i class="fas fa-download"></i> Скачать таблицу
                        </a>
                        <a href="${fileUrl}?download=false" class="btn btn-secondary" target="_blank">
                            <i class="fas fa-external-link-alt"></i> Открыть в новой вкладке
                        </a>
                    </div>
                    <div class="office-preview-message">
                        <p>Для просмотра содержимого таблицы Excel, пожалуйста, скачайте файл и откройте его в Microsoft Excel или другом совместимом приложении.</p>
                    </div>
                </div>
            </div>
        `;
        
        this.previewContainer.innerHTML = '';
        this.previewContainer.appendChild(excelContainer);
    }

    // Метод для предпросмотра PowerPoint документов
    previewPowerPoint(fileUrl, filename) {
        // Создаем контейнер для содержимого PowerPoint
        const pptContainer = document.createElement('div');
        pptContainer.className = 'preview-document preview-powerpoint';
        
        // Показываем информацию о презентации с вариантами действий
        pptContainer.innerHTML = `
            <div class="office-preview-container">
                <div class="office-preview-icon">
                    <i class="fas fa-file-powerpoint fa-4x"></i>
                </div>
                <div class="office-preview-info">
                    <h3>Презентация Microsoft PowerPoint</h3>
                    <p>Файл: ${filename}</p>
                    <div class="office-preview-actions">
                        <a href="${fileUrl}" class="btn btn-primary" download="${filename}">
                            <i class="fas fa-download"></i> Скачать презентацию
                        </a>
                        <a href="${fileUrl}?download=false" class="btn btn-secondary" target="_blank">
                            <i class="fas fa-external-link-alt"></i> Открыть в новой вкладке
                        </a>
                    </div>
                    <div class="office-preview-message">
                        <p>Для просмотра содержимого презентации PowerPoint, пожалуйста, скачайте файл и откройте его в Microsoft PowerPoint или другом совместимом приложении.</p>
                    </div>
                </div>
            </div>
        `;
        
        this.previewContainer.innerHTML = '';
        this.previewContainer.appendChild(pptContainer);
    }

    // Метод для отображения ошибки при предпросмотре Office документов
    showOfficeViewerError(container, fileUrl, filename, type) {
        const typeMap = {
            'word': { icon: 'fa-file-word', name: 'документа Word' },
            'excel': { icon: 'fa-file-excel', name: 'таблицы Excel' },
            'powerpoint': { icon: 'fa-file-powerpoint', name: 'презентации PowerPoint' }
        };
        
        const typeInfo = typeMap[type] || { icon: 'fa-file', name: 'документа' };
        
        container.innerHTML = `
            <div class="preview-error">
                <i class="fas ${typeInfo.icon}"></i>
                <p>Не удалось показать предпросмотр ${typeInfo.name}</p>
                <p>Возможно, сервер недоступен или документ слишком большой</p>
                <div class="office-actions">
                    <a href="${fileUrl}" class="btn btn-primary" target="_blank" download>
                        <i class="fas fa-download"></i> Скачать документ
                    </a>
                    <a href="https://docs.google.com/viewer?url=${encodeURIComponent(window.location.origin + fileUrl)}" class="btn btn-secondary" target="_blank">
                        <i class="fas fa-external-link-alt"></i> Открыть в Google Viewer
                    </a>
                </div>
            </div>
        `;
    }
    
    // Helper to determine the right Office icon
    getOfficeIcon(ext) {
        if (['doc', 'docx', 'docm', 'dot', 'dotx', 'dotm', 'rtf', 'odt'].includes(ext)) {
            return 'word';
        } else if (['xls', 'xlsx', 'xlsm', 'xlt', 'xltx', 'xltm', 'ods'].includes(ext)) {
            return 'excel';
        } else if (['ppt', 'pptx', 'pptm', 'pot', 'potx', 'potm', 'odp'].includes(ext)) {
            return 'powerpoint';
        } else if (['vsd', 'vsdx', 'vdw', 'vss', 'vssx'].includes(ext)) {
            return 'alt';
        } else if (['mdb', 'accdb', 'accde', 'accdt'].includes(ext)) {
            return 'database';
        } else {
            return 'document';
        }
    }
    
    // Show error state in preview
    showPreviewError() {
        const filename = this.previewableFiles[this.currentIndex];
        
        this.previewContainer.innerHTML = `
            <div class="preview-error">
                <i class="fas fa-exclamation-triangle"></i>
                <p>Не удалось показать предпросмотр для этого типа файлов</p>
                <a href="/${this.linkId}/download/${encodeURIComponent(filename)}" 
                   class="btn btn-primary" target="_blank" download>
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
    if (typeof linkId !== 'undefined') {
        window.previewManager = new FilePreviewManager(linkId);
        
        // Initialize preview buttons
        window.previewManager.initPreviewButtons();
    }
});

// Добавляем глобальную функцию для вызова из HTML
function showPreview(filename) {
    if (window.previewManager) {
        window.previewManager.showPreview(filename);
    } else {
        console.error('Preview manager is not initialized');
    }
}
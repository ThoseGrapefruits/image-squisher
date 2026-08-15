// image-squisher-automator.js
// JavaScript for Automation script for Automator Folder Action
// Set IMAGE_SQUISHER_HOME to the image-squisher repository path.

function shellQuote(value) {
    return "'" + String(value).replace(/'/g, "'\\''") + "'";
}

function run(input, parameters) {
    var folders = input;

    var app = Application.currentApplication();
    app.includeStandardAdditions = true;

    ObjC.import('stdlib');
    var scriptDir = $.getenv('IMAGE_SQUISHER_HOME');
    if (!scriptDir) {
        app.displayAlert('Image Squisher', {
            message: 'Set the IMAGE_SQUISHER_HOME environment variable to your image-squisher repository path.'
        });
        return [];
    }

    var pythonPath = scriptDir + '/venv/bin/python';
    var mainScript = scriptDir + '/main.py';

    var processedFolders = [];
    var errors = [];

    for (var i = 0; i < folders.length; i++) {
        var folderPath = String(folders[i]);
        var command = 'cd ' + shellQuote(scriptDir) + ' && ' + shellQuote(pythonPath) + ' ' + shellQuote(mainScript) + ' ' + shellQuote(folderPath);

        try {
            app.doShellScript(command);
            processedFolders.push(folderPath);
            app.doShellScript('terminal-notifier -title "Image Squisher" -message "Processing complete for: ' + folderPath.replace(/"/g, '\\"') + '" 2>/dev/null || true');
        } catch (error) {
            errors.push('Error processing ' + folderPath + ': ' + error.message);
            app.doShellScript('terminal-notifier -title "Image Squisher Error" -message "Error: ' + String(error.message).replace(/"/g, '\\"') + '" 2>/dev/null || true');
        }
    }

    if (errors.length > 0) {
        app.doShellScript('terminal-notifier -title "Image Squisher" -message "Completed with ' + errors.length + ' error(s). Check logs for details." 2>/dev/null || true');
    }

    return processedFolders;
}

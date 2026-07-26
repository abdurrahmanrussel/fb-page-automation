/**
 * AR TechLabs — Post Queue Apps Script.
 * Bind this to a Google Sheet (Extensions → Apps Script), deploy as a Web App,
 * and put the deployment URL in backend/.env as GOOGLE_SCRIPT_URL.
 *
 * Sheet layout: column A = queued post text, starting at row 2 (row 1 = header).
 * Images are NOT stored in the sheet — each time a post is served, a random
 * image from the Drive folder is attached automatically.
 *
 * Endpoints (all GET, matching backend/sheets.py + backend/add_post.py):
 *   ?                      → pop the next queued post + a random image, delete that row
 *   ?action=add&post=...   → append a post to the queue
 *   ?action=list           → list all queued posts
 *   ?action=clear          → delete all queued posts
 */

var FOLDER_ID = '1YVwkNkUGgAXTX-xkm0taohUt4R3T7Hi8'; // AR TechLabs public image folder

function doGet(e) {
  var action = e.parameter.action;
  var result;

  if (action === 'add') {
    result = addPost(e.parameter.post);
  } else if (action === 'list') {
    result = listPosts();
  } else if (action === 'clear') {
    result = clearQueue();
  } else {
    result = popNextPost();
  }

  return ContentService
    .createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}

function getSheet() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
}

function addPost(post) {
  if (!post) return { status: 'error', message: 'Missing post param' };
  var sheet = getSheet();
  sheet.appendRow([post]);
  return { status: 'added' };
}

function listPosts() {
  var sheet = getSheet();
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return { posts: [] };
  var values = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
  var posts = values
    .map(function (row) { return row[0]; })
    .filter(function (v) { return v !== '' && v !== null; })
    .map(function (v) { return { post: v }; });
  return { posts: posts };
}

function clearQueue() {
  var sheet = getSheet();
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return { deleted: 0 };
  var count = lastRow - 1;
  sheet.deleteRows(2, count);
  return { deleted: count };
}

function popNextPost() {
  var sheet = getSheet();
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return { post: null, image: null };

  var post = sheet.getRange(2, 1).getValue();
  sheet.deleteRow(2);

  if (!post) return { post: null, image: null };

  return { post: post, image: getRandomImage() };
}

function getRandomImage() {
  var folder = DriveApp.getFolderById(FOLDER_ID);
  var files = folder.getFiles();
  var fileIds = [];
  while (files.hasNext()) {
    fileIds.push(files.next().getId());
  }
  if (fileIds.length === 0) return null;

  var fileId = fileIds[Math.floor(Math.random() * fileIds.length)];
  return 'https://drive.google.com/file/d/' + fileId + '/view';
}

"""The sessions area: chat sessions imported from files or captured from a browser, and their refinement.

  controller.py        SessionsProductController, which the window reaches through sessions.* methods
  edge_visible_ui.py   reading the visible page of a chat site in Edge
  archive/             the SQLite archive of imported sessions and the importers of export formats
  companion/           the browser companion: installed browsers, site layouts, the extension
  refinement/          turning a conversation into typed items for review, and the model channel it
                       runs through

This file imports nothing, so code that needs only refinement (the library, card states) does not
load the controller.
"""

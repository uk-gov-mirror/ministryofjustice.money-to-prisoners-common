// Show-hide module

/* globals exports */
'use strict';

exports.ShowHide = {
  collapsedText: 'Expand',
  expandedText: 'Collapse',

  init: function () {
    $('.HistoryHeader').append('<span class="ShowHide HistoryHeader-aside print-hidden">'+this.expandedText+'</span>');

    this.$showHideButtons = $('.ShowHide');
    this.$showHideButtons.on('click', $.proxy(this.onShowHide, this));
  },

  onShowHide: function (e) {
    var $target = $(e.target);
    e.preventDefault();
    if ($target.hasClass('ShowHide-hidden')) {
      $target.html(this.expandedText);
      $target.removeClass('ShowHide-hidden');
    } else {
      $target.html(this.collapsedText);
      $target.addClass('ShowHide-hidden');
    }
    $target.closest('table').find('tbody,thead').toggle();

  }
};
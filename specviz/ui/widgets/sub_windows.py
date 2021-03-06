from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import sys, os
import logging
from functools import reduce

import numpy as np
import pyqtgraph as pg

from astropy.units import Unit, Quantity

from .axes import DynamicAxisItem
from ...third_party.qtpy.QtWidgets import *
from ...third_party.qtpy.QtGui import *
from ..widgets.dialogs import TopAxisDialog, UnitChangeDialog
from ..widgets.toolbars import PlotToolBar
from ..qt.plotsubwindow import Ui_SpectraSubWindow
from ...core.comms import Dispatch, DispatchHandle
from ...core.linelist import LineList
from .region_items import LinearRegionItem


pg.setConfigOption('background', 'w')
pg.setConfigOption('foreground', 'k')
pg.setConfigOptions(antialias=False)


class PlotSubWindow(QMainWindow):
    """
    Sub window object responsible for displaying and interacting with plots.
    """
    def __init__(self, **kwargs):
        super(PlotSubWindow, self).__init__(**kwargs)
        # Setup plot sub window ui
        self.ui_plot_sub_window = Ui_SpectraSubWindow()
        self.ui_plot_sub_window.setupUi(self)

        # Setup custom tool bar
        self._tool_bar = PlotToolBar()
        self.addToolBar(self._tool_bar)

        self._containers = []
        self._top_axis_dialog = TopAxisDialog()
        self._unit_change_dialog = UnitChangeDialog()
        self._dynamic_axis = None
        self._plot_widget = None
        self._plot_item = None
        self._plots_units = None
        self._rois = []
        self._measure_rois = []
        self._centroid_roi = None

        DispatchHandle.setup(self)

        self._dynamic_axis = DynamicAxisItem(orientation='top')
        self._plot_widget = pg.PlotWidget(
            axisItems={'top': self._dynamic_axis})
        self.setCentralWidget(self._plot_widget)

        self._plot_item = self._plot_widget.getPlotItem()
        self._plot_item.showAxis('top', True)
        # Add grids to the plot
        self._plot_item.showGrid(True, True)

        self._setup_connections()

    def _setup_connections(self):
        # Setup ROI connection
        self.ui_plot_sub_window.actionInsert_ROI.triggered.connect(self.add_roi)

        # On accept, change the displayed axis
        self._top_axis_dialog.accepted.connect(lambda:
            self.update_axis(
                self._containers[0].layer,
                self._top_axis_dialog.ui_axis_dialog.axisModeComboBox
                    .currentIndex(),
                redshift=self._top_axis_dialog.redshift,
                ref_wave=self._top_axis_dialog.ref_wave))

        # Setup equivalent width toggle
        self.ui_plot_sub_window.actionMeasure.triggered.connect(
            self._toggle_measure)

        # Tool bar connections
        self._tool_bar.atn_change_top_axis.triggered.connect(
            self._top_axis_dialog.exec_)

        self._tool_bar.atn_change_units.triggered.connect(
            self._show_unit_change_dialog)

        self._tool_bar.atn_line_ids.triggered.connect(
            self._show_line_ids)

    def _show_unit_change_dialog(self):
        if self._unit_change_dialog.exec_():
            x_text = self._unit_change_dialog.disp_unit
            y_text = self._unit_change_dialog.flux_unit

            x_unit = y_unit = None

            try:
                x_unit = Unit(x_text) if x_text else None
            except ValueError as e:
                logging.error(e)

            try:
                y_unit = Unit(y_text) if y_text else None
            except ValueError as e:
                logging.error(e)

            self.change_units(x_unit, y_unit)

            self._plot_item.update()

    def _show_line_ids(self):

        # find the wavelength range spanned by the spectrum
        # (or spectra) at hand. The range will be used to select
        # lines from the line list table(s).

        # increasing dispersion values!
        amin = sys.float_info.max
        amax = 0.0
        for container in self._containers:
            amin = min(amin, container.dispersion.value[0])
            amax = max(amax, container.dispersion.value[-1])

        amin = Quantity(amin, self._plot_units[0])
        amax = Quantity(amax, self._plot_units[0])

        # if file_name is None:
        #     file_name, selected_filter = self.viewer.open_file_dialog(
        #         loader_registry.filters)
        #
        # if not file_name:
        #     return

        # Lets skip the file dialog business for now. This is just a
        # proof-of-concept code. Later we will add more fanciness to it.
        #
        # Use these two tables for now. In the future, the filter strings
        # should somehow be handled by the file dialog itself.
        fnames = ['Common_stellar.txt', 'Common_nebular.txt']

        path = os.path.dirname(os.path.abspath(__file__))
        dir_path = path + '/../../data/linelists/'
        linelists = []

        for fname in fnames:
            path = dir_path + fname
            filter = fname.split('.')[0] + ' (*.txt *.dat)'

            linelist = LineList.read(path, filter)
            linelist = linelist.extract_range(amin, amax)

            linelists.append(linelist)

        linelist = LineList.merge(linelists)

        # try:
        #     data = data_manager.load(str(file_name), str(selected_filter))
        # except:
        #     logging.error("Incompatible loader for selected data.")

        # display line lists in a tabbed pane.

        # display line markers on plot sirface.

        Dispatch.on_added_linelist.emit(linelist=linelist)







    def _toggle_measure(self, on):
        if on:
            self.add_measure_rois()

            # Disable the ability to add new ROIs
            self.ui_plot_sub_window.actionInsert_ROI.setDisabled(True)
        else:
            self.remove_measure_rois()

            # Enable the ability to add new ROIs
            self.ui_plot_sub_window.actionInsert_ROI.setDisabled(False)


    def get_roi_mask(self, layer=None, container=None, roi=None):
        if layer is not None:
            container = self.get_container(layer)

        if container is None:
            return

        mask_holder = []
        rois = [roi] if roi is not None else self._rois

        for roi in rois:
            # roi_shape = roi.parentBounds()
            # x1, y1, x2, y2 = roi_shape.getCoords()
            x1, x2 = roi.getRegion()

            layer_mask = np.copy(layer._mask)
            mask = (container.dispersion.value >= x1) & \
                   (container.dispersion.value <= x2)
            layer_mask[layer_mask==True] = mask
            mask_holder.append(layer_mask)

        if len(mask_holder) == 0:
            mask_holder.append(container.layer._mask)

        # mask = np.logical_not(reduce(np.logical_or, mask_holder))
        mask = reduce(np.logical_or, mask_holder)
        return mask

    def add_roi(self):
        view_range = self._plot_item.viewRange()
        x_len = (view_range[0][1] - view_range[0][0]) * 0.5
        x_pos = x_len * 0.5 + view_range[0][0]

        def remove():
            self._plot_item.removeItem(roi)
            self._rois.remove(roi)

        roi = LinearRegionItem(values=[x_pos, x_pos + x_len])
        self._rois.append(roi)
        self._plot_item.addItem(roi)

        # Connect the remove functionality
        roi.sigRemoveRequested.connect(remove)

        # Connect events
        Dispatch.on_updated_roi.emit(roi=roi)
        roi.sigRemoveRequested.connect(
            lambda: Dispatch.on_updated_roi.emit(roi=roi))
        roi.sigRegionChangeFinished.connect(
            lambda: Dispatch.on_updated_roi.emit(roi=roi))

    def add_measure_rois(self):
        # First, remove existing rois
        for roi in self._rois:
            self._plot_item.removeItem(roi)

        if len(self._measure_rois) == 0:
            for i in range(3):
                view_range = self._plot_item.viewRange()
                x_vrange = view_range[0][1] - view_range[0][0]
                x_len = x_vrange * 0.25
                x_pos = view_range[0][0] + x_vrange * 0.1 + x_len * 1.1 * i

                roi = LinearRegionItem(values=[x_pos, x_pos + x_len],
                                       brush=pg.mkBrush(
                                           QColor(152, 251, 152, 50)),
                                       removable=False)

                if i == 1:
                    roi.setBrush(pg.mkBrush(QColor(255, 69, 0, 50)))

                self._measure_rois.append(roi)

            for roi in self._measure_rois:
                roi.sigRemoveRequested.connect(
                    lambda: Dispatch.on_updated_roi.emit(
                        measure_rois=self._measure_rois))
                roi.sigRegionChangeFinished.connect(
                    lambda: Dispatch.on_updated_roi.emit(
                        measure_rois=self._measure_rois))

            # Connect events
            Dispatch.on_updated_roi.emit(measure_rois=self._measure_rois)

        for roi in self._measure_rois:
            self._plot_item.addItem(roi)

    def remove_measure_rois(self):
        for roi in self._measure_rois:
            self._plot_item.removeItem(roi)

        # Replace rois we removed
        for roi in self._rois:
            self._plot_item.addItem(roi)

    def get_container(self, layer):
        for container in self._containers:
            if container.layer == layer:
                return container

    def change_units(self, x=None, y=None, z=None):
        for cntr in self._containers:
            cntr.change_units(x, y, z)

        self.set_labels(x_label=x, y_label=y)
        self._plot_item.enableAutoRange()
        self._plot_units = [x, y, z]

    def set_labels(self, x_label='', y_label=''):
        self._plot_item.setLabels(
            left="Flux [{}]".format(
                y_label or str(self._containers[0].layer.units[1])),
            bottom="Wavelength [{}]".format(
                x_label or str(self._containers[0].layer.units[0])))

    def set_visibility(self, layer, show, override=False):
        for container in self._containers:
            if container.layer == layer:
                if container._visibility_state != [show, show, False]:
                    container.set_visibility(show, show, inactive=False,
                                             override=override)

    def update_axis(self, layer=None, mode=None, **kwargs):
        self._dynamic_axis.update_axis(layer, mode, **kwargs)
        self._plot_widget.update()

    def closeEvent(self, event):
        DispatchHandle.tear_down(self)
        super(PlotSubWindow, self).closeEvent(event)

    @DispatchHandle.register_listener("on_added_plot")
    def add_container(self, container=None, window=None):
        if window != self:
            return

        # User tries to plot before loading file, do nothing
        if not hasattr(container, 'layer'):
            return

        if len(self._containers) == 0:
            self.change_units(container.layer.units[0],
                              container.layer.units[1])
        else:
            container.change_units(*self._plot_units)

        if container.error is not None:
            self._plot_item.addItem(container.error)

        self._containers.append(container)
        self._plot_item.addItem(container.plot)

        self.set_active_plot(container.layer)

        # Make sure the dynamic axis object has access to a layer
        self._dynamic_axis._layer = self._containers[0].layer

    @DispatchHandle.register_listener("on_removed_plot")
    def remove_container(self, layer, window=None):
        if window is not None and window != self:
            return

        for container in [x for x in self._containers]:
            if container.layer == layer:
                self._plot_item.removeItem(container.plot)

                if container.error is not None:
                    self._plot_item.removeItem(container.error)

                self._containers.remove(container)

    @DispatchHandle.register_listener("on_selected_plot")
    def set_active_plot(self, layer):
        for container in self._containers:
            if container.layer == layer:
                container.set_visibility(True, True, inactive=False)
            else:
                container.set_visibility(True, False, inactive=True)


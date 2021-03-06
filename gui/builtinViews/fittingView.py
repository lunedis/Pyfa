#===============================================================================
# Copyright (C) 2010 Diego Duclos
#
# This file is part of pyfa.
#
# pyfa is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# pyfa is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pyfa.  If not, see <http://www.gnu.org/licenses/>.
#===============================================================================

import wx
import wx.lib.newevent
import service
import gui.mainFrame
import gui.marketBrowser
import gui.display as d
from gui.contextMenu import ContextMenu
import gui.shipBrowser
import gui.multiSwitch
from eos.types import Slot, Rack, Module
from gui.builtinViewColumns.state import State
from gui.bitmapLoader import BitmapLoader
import gui.builtinViews.emptyView
from gui.utils.exportHtml import exportHtml

import gui.globalEvents as GE

#Tab spawning handler
class FitSpawner(gui.multiSwitch.TabSpawner):
    def __init__(self, multiSwitch):
        self.multiSwitch = multiSwitch
        self.mainFrame = mainFrame = gui.mainFrame.MainFrame.getInstance()
        mainFrame.Bind(gui.shipBrowser.EVT_FIT_SELECTED, self.fitSelected)
        self.multiSwitch.tabsContainer.handleDrag = self.handleDrag

    def fitSelected(self, event):
        count = -1
        for index, page in enumerate(self.multiSwitch.pages):
            try:
                if page.activeFitID == event.fitID:
                    count += 1
                    self.multiSwitch.SetSelection(index)
                    wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=event.fitID))
                    break
            except:
                pass
        if count < 0:
            startup = getattr(event, "startup", False)  # see OpenFitsThread in gui.mainFrame
            mstate = wx.GetMouseState()

            if mstate.CmdDown() or startup:
                self.multiSwitch.AddPage()

            view = FittingView(self.multiSwitch)
            self.multiSwitch.ReplaceActivePage(view)
            view.fitSelected(event)

    def handleDrag(self, type, fitID):
        if type == "fit":
            for page in self.multiSwitch.pages:
                if isinstance(page, FittingView) and page.activeFitID == fitID:
                    index = self.multiSwitch.GetPageIndex(page)
                    self.multiSwitch.SetSelection(index)
                    wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=fitID))
                    return
                elif isinstance(page, gui.builtinViews.emptyView.BlankPage):
                    view = FittingView(self.multiSwitch)
                    self.multiSwitch.ReplaceActivePage(view)
                    view.handleDrag(type, fitID)
                    return

            view = FittingView(self.multiSwitch)
            self.multiSwitch.AddPage(view)
            view.handleDrag(type, fitID)

FitSpawner.register()

#Drag'n'drop handler
class FittingViewDrop(wx.PyDropTarget):
        def __init__(self, dropFn):
            wx.PyDropTarget.__init__(self)
            self.dropFn = dropFn
            # this is really transferring an EVE itemID
            self.dropData = wx.PyTextDataObject()
            self.SetDataObject(self.dropData)

        def OnData(self, x, y, t):
            if self.GetData():
                data = self.dropData.GetText().split(':')
                self.dropFn(x, y, data)
            return t

class FittingView(d.Display):
    DEFAULT_COLS = ["State",
                    "Ammo Icon",
                    "Base Icon",
                    "Base Name",
                    "attr:power",
                    "attr:cpu",
                    "Capacitor Usage",
                    "Max Range",
                    "Miscellanea",
                    "Price",
                    "Ammo",
                    ]

    def __init__(self, parent):
        d.Display.__init__(self, parent, size = (0,0), style =  wx.BORDER_NONE)
        self.Show(False)
        self.parent = parent
        self.mainFrame.Bind(GE.FIT_CHANGED, self.fitChanged)
        self.mainFrame.Bind(gui.shipBrowser.EVT_FIT_RENAMED, self.fitRenamed)
        self.mainFrame.Bind(gui.shipBrowser.EVT_FIT_REMOVED, self.fitRemoved)
        self.mainFrame.Bind(gui.marketBrowser.ITEM_SELECTED, self.appendItem)

        self.Bind(wx.EVT_LEFT_DCLICK, self.removeItem)
        self.Bind(wx.EVT_LIST_BEGIN_DRAG, self.startDrag)
        if "__WXGTK__" in  wx.PlatformInfo:
            self.Bind(wx.EVT_RIGHT_UP, self.scheduleMenu)
        else:
            self.Bind(wx.EVT_RIGHT_DOWN, self.scheduleMenu)

        self.SetDropTarget(FittingViewDrop(self.handleListDrag))
        self.activeFitID = None
        self.FVsnapshot = None
        self.itemCount = 0
        self.itemRect = 0

        self.hoveredRow = None
        self.hoveredColumn = None

        self.Bind(wx.EVT_KEY_UP, self.kbEvent)
        self.Bind(wx.EVT_LEFT_DOWN, self.click)
        self.Bind(wx.EVT_RIGHT_DOWN, self.click)
        self.Bind(wx.EVT_MIDDLE_DOWN, self.click)
        self.Bind(wx.EVT_SHOW, self.OnShow)
        self.Bind(wx.EVT_MOTION, self.OnMouseMove)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeaveWindow)
        self.parent.Bind(gui.chromeTabs.EVT_NOTEBOOK_PAGE_CHANGED, self.pageChanged)

    def OnLeaveWindow(self, event):
        self.SetToolTip(None)
        self.hoveredRow = None
        self.hoveredColumn = None
        event.Skip()

    def OnMouseMove(self, event):
        row, _, col = self.HitTestSubItem(event.Position)
        if row != self.hoveredRow or col != self.hoveredColumn:
            if self.ToolTip is not None:
                self.SetToolTip(None)
            else:
                self.hoveredRow = row
                self.hoveredColumn = col
                if row != -1 and row not in self.blanks and col != -1 and col < len(self.DEFAULT_COLS):
                    mod = self.mods[self.GetItemData(row)]
                    tooltip = self.activeColumns[col].getToolTip(mod)
                    if tooltip is not None:
                        self.SetToolTipString(tooltip)
                    else:
                        self.SetToolTip(None)
                else:
                    self.SetToolTip(None)
        event.Skip()

    def handleListDrag(self, x, y, data):
        '''
        Handles dragging of items from various pyfa displays which support it

        data is list with two items:
            data[0] is hard-coded str of originating source
            data[1] is typeID or index of data we want to manipulate
        '''

        if data[0] == "fitting":
            self.swapItems(x, y, int(data[1]))
        elif data[0] == "cargo":
            self.swapCargo(x, y, int(data[1]))
        elif data[0] == "market":
            wx.PostEvent(self.mainFrame, gui.marketBrowser.ItemSelected(itemID=int(data[1])))

    def handleDrag(self, type, fitID):
        #Those are drags coming from pyfa sources, NOT builtin wx drags
        if type == "fit":
            wx.PostEvent(self.mainFrame, gui.shipBrowser.FitSelected(fitID=fitID))

    def Destroy(self):
        self.parent.Unbind(gui.chromeTabs.EVT_NOTEBOOK_PAGE_CHANGED, handler=self.pageChanged)
        self.mainFrame.Unbind(GE.FIT_CHANGED, handler=self.fitChanged)
        self.mainFrame.Unbind(gui.shipBrowser.EVT_FIT_RENAMED, handler=self.fitRenamed)
        self.mainFrame.Unbind(gui.shipBrowser.EVT_FIT_REMOVED, handler=self.fitRemoved)
        self.mainFrame.Unbind(gui.marketBrowser.ITEM_SELECTED, handler=self.appendItem)

        d.Display.Destroy(self)

    def pageChanged(self, event):
        if self.parent.IsActive(self):
            fitID = self.getActiveFit()
            sFit = service.Fit.getInstance()
            sFit.switchFit(fitID)
            wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=fitID))

        event.Skip()

    def getActiveFit(self):
        return self.activeFitID

    def startDrag(self, event):
        row = event.GetIndex()

        if row != -1 and row not in self.blanks:
            data = wx.PyTextDataObject()
            data.SetText("fitting:"+str(self.mods[row].position))

            dropSource = wx.DropSource(self)
            dropSource.SetData(data)
            res = dropSource.DoDragDrop()

    def getSelectedMods(self):
        sel = []
        row = self.GetFirstSelected()
        while row != -1:
            sel.append(self.mods[self.GetItemData(row)])
            row = self.GetNextSelected(row)

        return sel

    def kbEvent(self,event):
        keycode = event.GetKeyCode()
        if keycode == wx.WXK_DELETE or keycode == wx.WXK_NUMPAD_DELETE:
            row = self.GetFirstSelected()
            firstSel = row
            while row != -1:
                if row not in self.blanks:
                    self.removeModule(self.mods[row])
                self.Select(row,0)
                row = self.GetNextSelected(row)

        event.Skip()

    def fitRemoved(self, event):
        '''
        If fit is removed and active, the page is deleted.
        We also refresh the fit of the new current page in case
        delete fit caused change in stats (projected)
        '''
        fitID = event.fitID

        if fitID == self.getActiveFit():
            self.parent.DeletePage(self.parent.GetPageIndex(self))

        try:
            # Sometimes there is no active page after deletion, hence the try block
            sFit = service.Fit.getInstance()
            sFit.refreshFit(self.getActiveFit())
            wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=self.activeFitID))
        except wx._core.PyDeadObjectError:
            pass

        event.Skip()

    def fitRenamed(self, event):
        fitID = event.fitID
        if fitID == self.getActiveFit():
            self.updateTab()

        event.Skip()

    def fitSelected(self, event):
        if self.parent.IsActive(self):
            fitID = event.fitID
            startup = getattr(event, "startup", False)
            self.activeFitID = fitID
            sFit = service.Fit.getInstance()
            self.updateTab()
            if not startup or startup == 2:  # see OpenFitsThread in gui.mainFrame
                self.Show(fitID is not None)
                self.slotsChanged()
                sFit.switchFit(fitID)
                wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=fitID))

        event.Skip()

    def updateTab(self):
        sFit = service.Fit.getInstance()
        fit = sFit.getFit(self.getActiveFit(), basic=True)

        bitmap = BitmapLoader.getImage("race_%s_small" % fit.ship.item.race, "gui")
        text = "%s: %s" % (fit.ship.item.name, fit.name)

        pageIndex = self.parent.GetPageIndex(self)
        if pageIndex is not None:
            self.parent.SetPageTextIcon(pageIndex, text, bitmap)

    def appendItem(self, event):
        if self.parent.IsActive(self):
            itemID = event.itemID
            fitID = self.activeFitID
            if fitID != None:
                sFit = service.Fit.getInstance()
                if sFit.isAmmo(itemID):
                    modules = []
                    sel = self.GetFirstSelected()
                    while sel != -1 and sel not in self.blanks:
                        modules.append(self.mods[self.GetItemData(sel)])
                        sel = self.GetNextSelected(sel)

                    sFit.setAmmo(fitID, itemID, modules)
                    wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=fitID))
                else:
                    populate = sFit.appendModule(fitID, itemID)
                    if populate is not None:
                        self.slotsChanged()
                        wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=fitID))

        event.Skip()

    def removeItem(self, event):
        row, _ = self.HitTest(event.Position)
        if row != -1 and row not in self.blanks:
            col = self.getColumn(event.Position)
            if col != self.getColIndex(State):
                self.removeModule(self.mods[row])
            else:
                if "wxMSW" in wx.PlatformInfo:
                    self.click(event)

    def removeModule(self, module):
        sFit = service.Fit.getInstance()
        fit = sFit.getFit(self.activeFitID)
        populate = sFit.removeModule(self.activeFitID, fit.modules.index(module))

        if populate is not None:
            self.slotsChanged()
            wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=self.activeFitID))

    def swapCargo(self, x, y, srcIdx):
        '''Swap a module from cargo to fitting window'''
        mstate = wx.GetMouseState()

        dstRow, _ = self.HitTest((x, y))
        if dstRow != -1 and dstRow not in self.blanks:
            module = self.mods[dstRow]

            sFit = service.Fit.getInstance()
            sFit.moveCargoToModule(self.mainFrame.getActiveFit(), module.position, srcIdx, mstate.CmdDown() and module.isEmpty)

            wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=self.mainFrame.getActiveFit()))

    def swapItems(self, x, y, srcIdx):
        '''Swap two modules in fitting window'''
        mstate = wx.GetMouseState()
        sFit = service.Fit.getInstance()
        fit = sFit.getFit(self.activeFitID)

        if mstate.CmdDown():
            clone = True
        else:
            clone = False

        dstRow, _ = self.HitTest((x, y))

        if dstRow != -1 and dstRow not in self.blanks:
            mod1 = fit.modules[srcIdx]
            mod2 = self.mods[dstRow]

            # can't swap modules to different racks
            if mod1.slot != mod2.slot:
                return

            if clone and mod2.isEmpty:
                sFit.cloneModule(self.mainFrame.getActiveFit(), mod1.position, mod2.position)
            else:
                sFit.swapModules(self.mainFrame.getActiveFit(), mod1.position, mod2.position)

            wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=self.mainFrame.getActiveFit()))

    def generateMods(self):
        '''
        Generate module list.

        This also injects dummy modules to visually separate racks. These modules are only
        known to the display, and not the backend, so it's safe.
        '''

        sFit = service.Fit.getInstance()
        fit = sFit.getFit(self.activeFitID)

        slotOrder = [Slot.SUBSYSTEM, Slot.HIGH, Slot.MED, Slot.LOW, Slot.RIG]

        if fit is not None:
            self.mods = fit.modules[:]
            self.mods.sort(key=lambda mod: (slotOrder.index(mod.slot), mod.position))

            # Blanks is a list of indexes that mark non-module positions (such
            # as Racks and tactical Modes. This allows us to skip over common
            # module operations such as swapping, removing, copying, etc. that
            # would otherwise cause complications
            self.blanks = []   # preliminary markers where blanks will be inserted

            if sFit.serviceFittingOptions["rackSlots"]:
                # flag to know when to add blanks, based on previous slot
                slotDivider = None if sFit.serviceFittingOptions["rackLabels"] else self.mods[0].slot

                # first loop finds where slot dividers must go before modifying self.mods
                for i, mod in enumerate(self.mods):
                    if mod.slot != slotDivider:
                        slotDivider = mod.slot
                        self.blanks.append((i, slotDivider)) # where and what

                # second loop modifies self.mods, rewrites self.blanks to represent actual index of blanks
                for i, (x, slot) in enumerate(self.blanks):
                    self.blanks[i] = x+i # modify blanks with actual index
                    self.mods.insert(x+i, Rack.buildRack(slot))

            if fit.mode:
                # Modes are special snowflakes and need a little manual loving
                # We basically append the Mode rack and Mode to the modules
                # while also marking their positions in the Blanks list
                if sFit.serviceFittingOptions["rackSlots"]:
                    self.blanks.append(len(self.mods))
                    self.mods.append(Rack.buildRack(Slot.MODE))

                self.blanks.append(len(self.mods))
                self.mods.append(fit.mode)
        else:
            self.mods = None

    def slotsChanged(self):
        self.generateMods()
        self.populate(self.mods)

    def fitChanged(self, event):
        try:
            if self.activeFitID is not None and self.activeFitID == event.fitID:
                self.generateMods()
                if self.GetItemCount() != len(self.mods):
                    # This only happens when turning on/off slot divisions
                    self.populate(self.mods)
                self.refresh(self.mods)

                exportHtml.getInstance().refreshFittingHtml()

            self.Show(self.activeFitID is not None and self.activeFitID == event.fitID)
        except wx._core.PyDeadObjectError:
            pass
        finally:
            event.Skip()

    def scheduleMenu(self, event):
        event.Skip()
        if self.getColumn(event.Position) != self.getColIndex(State):
            wx.CallAfter(self.spawnMenu)

    def spawnMenu(self):
        if self.activeFitID is None:
            return

        sMkt = service.Market.getInstance()
        selection = []
        sel = self.GetFirstSelected()
        contexts = []

        while sel != -1 and sel not in self.blanks:
            mod = self.mods[self.GetItemData(sel)]
            if not mod.isEmpty:
                srcContext = "fittingModule"
                itemContext = sMkt.getCategoryByItem(mod.item).name
                fullContext = (srcContext, itemContext)
                if not srcContext in tuple(fCtxt[0] for fCtxt in contexts):
                    contexts.append(fullContext)
                if mod.charge is not None:
                    srcContext = "fittingCharge"
                    itemContext = sMkt.getCategoryByItem(mod.charge).name
                    fullContext = (srcContext, itemContext)
                    if not srcContext in tuple(fCtxt[0] for fCtxt in contexts):
                        contexts.append(fullContext)

                selection.append(mod)

            sel = self.GetNextSelected(sel)

        contexts.append(("fittingShip", "Ship"))

        menu = ContextMenu.getMenu(selection, *contexts)
        self.PopupMenu(menu)

    def click(self, event):
        '''
        Handle click event on modules.

        This is only useful for the State column. If multiple items are selected,
        and we have clicked the State column, iterate through the selections and
        change State
        '''
        row, _, col = self.HitTestSubItem(event.Position)

        # only do State column and ignore invalid rows
        if row != -1 and row not in self.blanks and col == self.getColIndex(State):
            sel = []
            curr = self.GetFirstSelected()

            while curr != -1 and row not in self.blanks :
                sel.append(curr)
                curr = self.GetNextSelected(curr)

            if row not in sel:
                mods = [self.mods[self.GetItemData(row)]]
            else:
                mods = self.getSelectedMods()

            sFit = service.Fit.getInstance()
            fitID = self.mainFrame.getActiveFit()
            ctrl = wx.GetMouseState().CmdDown() or wx.GetMouseState().MiddleDown()
            click = "ctrl" if ctrl is True else "right" if event.GetButton() == 3 else "left"
            sFit.toggleModulesState(fitID, self.mods[self.GetItemData(row)], mods, click)

            # update state tooltip
            tooltip = self.activeColumns[col].getToolTip(self.mods[self.GetItemData(row)])
            if tooltip:
                self.SetToolTipString(tooltip)

            wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=self.mainFrame.getActiveFit()))
        else:
            event.Skip()

    slotColourMap = {1: wx.Colour(250, 235, 204),  # yellow = low slots
                     2: wx.Colour(188, 215, 241),  # blue   = mid slots
                     3: wx.Colour(235, 204, 209),  # red    = high slots
                     4: '',
                     5: ''}

    def slotColour(self, slot):
        return self.slotColourMap[slot] or self.GetBackgroundColour()

    def refresh(self, stuff):
        '''
        Displays fitting

        Sends data to d.Display.refresh where the rows and columns are set up, then does a
        bit of post-processing (colors)
        '''
        self.Freeze()
        d.Display.refresh(self, stuff)

        sFit = service.Fit.getInstance()
        fit = sFit.getFit(self.activeFitID)
        slotMap = {}

        # test for too many modules (happens with t3s / CCP change in slot layout)
        for slotType in Slot.getTypes():
            slot = Slot.getValue(slotType)
            slotMap[slot] = fit.getSlotsFree(slot) < 0

        font = (self.GetClassDefaultAttributes()).font
        for i, mod in enumerate(self.mods):
            self.SetItemBackgroundColour(i, self.GetBackgroundColour())

            #  only consider changing color if we're dealing with a Module
            if type(mod) is Module:
                if slotMap[mod.slot]:  # Color too many modules as red
                    self.SetItemBackgroundColour(i, wx.Colour(204, 51, 51))
                elif sFit.serviceFittingOptions["colorFitBySlot"]:  # Color by slot it enabled
                    self.SetItemBackgroundColour(i, self.slotColour(mod.slot))

            # Set rack face to bold
            if isinstance(mod, Rack) and \
                    sFit.serviceFittingOptions["rackSlots"] and \
                    sFit.serviceFittingOptions["rackLabels"]:
                font.SetWeight(wx.FONTWEIGHT_BOLD)
                self.SetItemFont(i, font)
            else:
                font.SetWeight(wx.FONTWEIGHT_NORMAL)
                self.SetItemFont(i, font)

        self.Thaw()
        self.itemCount = self.GetItemCount()
        self.itemRect = self.GetItemRect(0)

        if 'wxMac' in wx.PlatformInfo:
            try:
                self.MakeSnapshot()
            except:
               pass

    def OnShow(self, event):
        if event.GetShow():
            try:
                self.MakeSnapshot()
            except:
                pass
        event.Skip()

    def Snapshot(self):
        return self.FVsnapshot

    def MakeSnapshot(self, maxColumns = 1337):

        if self.FVsnapshot:
            del self.FVsnapshot

        tbmp = wx.EmptyBitmap(16,16)
        tdc = wx.MemoryDC()
        tdc.SelectObject(tbmp)
        font = wx.SystemSettings_GetFont(wx.SYS_DEFAULT_GUI_FONT)
        tdc.SetFont(font)

        columnsWidths = []
        for i in xrange(len(self.DEFAULT_COLS)):
            columnsWidths.append(0)

        sFit = service.Fit.getInstance()
        try:
            fit = sFit.getFit(self.activeFitID)
        except:
            return

        if fit is None:
            return

        slotMap = {}
        for slotType in Slot.getTypes():
            slot = Slot.getValue(slotType)
            slotMap[slot] = fit.getSlotsFree(slot) < 0

        padding = 2
        isize = 16
        headerSize = max(isize, tdc.GetTextExtent("W")[0]) + padding * 2

        maxWidth = 0
        maxRowHeight = isize
        rows = 0
        for id,st in enumerate(self.mods):
            for i, col in enumerate(self.activeColumns):
                if i>maxColumns:
                    break
                name = col.getText(st)

                if not isinstance(name, basestring):
                    name = ""

                nx,ny = tdc.GetTextExtent(name)
                imgId = col.getImageId(st)
                cw = 0
                if imgId != -1:
                    cw += isize + padding
                if name != "":
                    cw += nx + 4*padding

                if imgId == -1 and name == "":
                    cw += isize +padding

                maxRowHeight = max(ny, maxRowHeight)
                columnsWidths[i] = max(columnsWidths[i], cw)

            rows += 1

        render = wx.RendererNative.Get()

        #Fix column widths (use biggest between header or items)

        for i, col in enumerate(self.activeColumns):
            if i > maxColumns:
                break

            name = col.columnText
            imgId = col.imageId

            if not isinstance(name, basestring):
                name = ""

            opts = wx.HeaderButtonParams()

            if name != "":
                opts.m_labelText = name

            if imgId != -1:
                opts.m_labelBitmap = wx.EmptyBitmap(isize,isize)

            width = render.DrawHeaderButton(self, tdc, (0, 0, 16, 16),
                                sortArrow = wx.HDR_SORT_ICON_NONE, params = opts)

            columnsWidths[i] = max(columnsWidths[i], width)

        tdc.SelectObject(wx.NullBitmap)


        maxWidth = padding * 2

        for i in xrange(len(self.DEFAULT_COLS)):
            if i > maxColumns:
                break
            maxWidth += columnsWidths[i]


        mdc = wx.MemoryDC()
        mbmp = wx.EmptyBitmap(maxWidth, (maxRowHeight) * rows + padding*4 + headerSize)

        mdc.SelectObject(mbmp)

        mdc.SetBackground(wx.Brush(wx.SystemSettings_GetColour(wx.SYS_COLOUR_WINDOW)))
        mdc.Clear()

        mdc.SetFont(font)
        mdc.SetTextForeground(wx.SystemSettings_GetColour(wx.SYS_COLOUR_WINDOWTEXT))

        cx = padding
        for i, col in enumerate(self.activeColumns):
            if i > maxColumns:
                break

            name = col.columnText
            imgId = col.imageId

            if not isinstance(name, basestring):
                name = ""

            opts = wx.HeaderButtonParams()
            opts.m_labelAlignment = wx.ALIGN_LEFT
            if name != "":
                opts.m_labelText = name

            if imgId != -1:
                bmp = col.bitmap
                opts.m_labelBitmap = bmp

            width = render.DrawHeaderButton (self, mdc, (cx, padding, columnsWidths[i], headerSize), wx.CONTROL_CURRENT,
                                sortArrow = wx.HDR_SORT_ICON_NONE, params = opts)

            cx += columnsWidths[i]

        brush = wx.Brush(wx.Colour(224, 51, 51))
        pen = wx.Pen(wx.Colour(224, 51, 51))

        mdc.SetPen(pen)
        mdc.SetBrush(brush)

        cy = padding*2 + headerSize
        for id,st in enumerate(self.mods):
            cx = padding

            if slotMap[st.slot]:
                mdc.DrawRectangle(cx,cy,maxWidth - cx,maxRowHeight)

            for i, col in enumerate(self.activeColumns):
                if i>maxColumns:
                    break

                name = col.getText(st)
                if not isinstance(name, basestring):
                    name = ""

                imgId = col.getImageId(st)
                tcx = cx

                if imgId != -1:
                    self.imageList.Draw(imgId,mdc,cx,cy,wx.IMAGELIST_DRAW_TRANSPARENT,False)
                    tcx += isize + padding

                if name != "":
                    nx,ny = mdc.GetTextExtent(name)
                    rect = wx.Rect()
                    rect.top = cy
                    rect.left = cx + 2*padding
                    rect.width = nx
                    rect.height = maxRowHeight + padding
                    mdc.DrawLabel(name, rect, wx.ALIGN_CENTER_VERTICAL)
                    tcx += nx + padding

                cx += columnsWidths[i]

            cy += maxRowHeight

        mdc.SelectObject(wx.NullBitmap)

        self.FVsnapshot = mbmp

# -*- coding: utf-8 -*-
import asyncio
import base64
import json
import logging
import os
import pathlib
import random
import sqlite3
import webbrowser
import xml.etree.ElementTree
from unittest.mock import MagicMock, patch

import aiohttp
import shortuuid
from aiohttp import web
from cbpi.api import *
from cbpi.api.base import CBPiBase
from cbpi.api.config import ConfigType
from cbpi.api.dataclasses import NotificationAction, NotificationType
from cbpi.controller.kettle_controller import KettleController
from voluptuous.schema_builder import message

logger = logging.getLogger(__name__)


class RecipeCreation(CBPiExtension):
    def __init__(self, cbpi):
        self.cbpi = cbpi
        path = os.path.dirname(__file__)

        # register new route for recipe creation
        # this needs to be set in the parameter RECIPE_CREATION_PATH in the global cbpi setting to be able to use the plugin
        # After the change, the plugin replaces the recipe cbpi4 conmtroller for recipe creation
        self.cbpi.register(self, "/creation")
        self._task = asyncio.create_task(self.run())

    async def run(self):
        logger.info("Starting Recipe Import Plugin")
        if os.path.exists(os.path.join(".", "config", "upload")) is False:
            logger.info("Creating Upload folder")
            pathlib.Path(os.path.join(".", "config/upload")).mkdir(
                parents=True, exist_ok=True
            )
        await self.RecipeSettings()
        pass

    async def RecipeSettings(self):
        TEMP_UNIT = self.cbpi.config.get("TEMP_UNIT", "C")
        self.default_boil_temp = 99 if TEMP_UNIT == "C" else 212
        self.default_cool_temp = 20 if TEMP_UNIT == "C" else 68

    # register individual routes for each recipe source (they will use the path under '/creation' from above)
    @request_mapping(path="/kbh", method="POST", auth_required=False)
    async def create_kbh_recipe(self, request):
        kbh_id = await request.json()
        await self.kbh_recipe_creation(kbh_id["id"])
        return web.Response(status=200)

    @request_mapping(path="/xml", method="POST", auth_required=False)
    async def create_xml_recipe(self, request):
        xml_id = await request.json()
        await self.xml_recipe_creation(xml_id["id"])
        return web.Response(status=200)

    @request_mapping(path="/bf", method="POST", auth_required=False)
    async def create_bf_recipe(self, request):
        bf_id = await request.json()
        await self.bf_recipe_creation(bf_id["id"])
        return web.Response(status=200)

    @request_mapping(path="/json", method="POST", auth_required=False)
    async def create_json_recipe(self, request):
        json_id = await request.json()
        await self.json_recipe_creation(json_id["id"])
        return web.Response(status=200)

    # function to create a recipe from the Kleiner Brauhelfer database
    async def kbh_recipe_creation(self, Recipe_ID):
        config = self.get_config_values()
        if self.kettle is not None:
            # check if kbh database is available in the upload folder and connect to it
            self.path = self.cbpi.config_folder.get_upload_file("kbh.db")
            if os.path.exists(self.path) is False:
                self.cbpi.notify(
                    "File Not Found",
                    "Please upload a kbh V2 database file",
                    NotificationType.ERROR,
                )

            try:
                # Get Recipe Nmae
                conn = sqlite3.connect(self.path)
                c = conn.cursor()
                c.execute("SELECT Sudname FROM Sud WHERE ID = ?", (Recipe_ID,))
                row = c.fetchone()
                name = row[0]
                # get MashIn Temp
                mashin_temp = None
                c.execute(
                    "SELECT TempWasser FROM Maischplan WHERE Typ = 0 AND SudID = ?",
                    (Recipe_ID,),
                )
                row = c.fetchone()
                try:
                    if self.cbpi.config.get("TEMP_UNIT", "C") == "C":
                        mashin_temp = str(int(row[0]))
                    else:
                        mashin_temp = str(round(9.0 / 5.0 * int(row[0]) + 32))
                except:
                    pass
                # get the hop addition times
                c.execute(
                    "SELECT Zeit, Name FROM Hopfengaben WHERE Vorderwuerze <> 1 AND SudID = ?",
                    (Recipe_ID,),
                )
                hops = c.fetchall()
                whirlpool = []
                for hop in hops:
                    if hop[0] < 0:
                        whirlpool.append(hop)
                for whirl in whirlpool:
                    hops.remove(whirl)
                # get the misc addition times
                c.execute(
                    "SELECT Zugabedauer, Name FROM WeitereZutatenGaben WHERE Zeitpunkt = 1 AND SudID = ?",
                    (Recipe_ID,),
                )
                miscs = c.fetchall()
                try:
                    c.execute(
                        "SELECT Zeit, Name FROM Hopfengaben WHERE Vorderwuerze = 1 AND SudID = ?",
                        (Recipe_ID,),
                    )
                    FW_Hops = c.fetchall()
                    FirstWort = self.getFirstWort(FW_Hops, "kbh")
                except:
                    FirstWort = "No"

                # get the boiltime from the database
                c.execute("SELECT Kochdauer FROM Sud WHERE ID = ?", (Recipe_ID,))
                row = c.fetchone()
                BoilTime = str(int(row[0]))

                await self.create_recipe(name)

                # create a mashin step if mashin_temp is available (just heating up to temp and waiting for user input)
                if mashin_temp is not None:
                    step_type = self.mashin if self.mashin != "" else "MashInStep"
                    step_string = {
                        "name": "MashIn",
                        "props": {
                            "AutoMode": self.AutoMode,
                            "Kettle": self.id,
                            "Sensor": self.kettle.sensor,
                            "Temp": mashin_temp,
                            "Timer": "0",
                            "Notification": "Target temperature reached. Please add malt.",
                        },
                        "status_text": "",
                        "status": "I",
                        "type": step_type,
                    }
                    await self.create_step(step_string)

                # if mashin_temp is not available, create a mashin step with the temp of the first mash step if addmashin is set to yes
                for row in c.execute(
                    "SELECT Name, TempRast, DauerRast FROM Maischplan WHERE Typ <> 0 AND SudID = ?",
                    (Recipe_ID,),
                ):
                    if mashin_temp is None and self.addmashin == "Yes":
                        step_type = self.mashin if self.mashin != "" else "MashInStep"
                        step_string = {
                            "name": "MashIn",
                            "props": {
                                "AutoMode": self.AutoMode,
                                "Kettle": self.id,
                                "Sensor": self.kettle.sensor,
                                "Temp": (
                                    str(int(row[1]))
                                    if self.TEMP_UNIT == "C"
                                    else str(round(9.0 / 5.0 * int(row[1]) + 32))
                                ),
                                "Timer": "0",
                                "Notification": "Target temperature reached. Please add malt.",
                            },
                            "status_text": "",
                            "status": "I",
                            "type": step_type,
                        }
                        await self.create_step(step_string)

                    # create the mash steps based on the recipe settings (time and temp)
                    step_type = self.mash if self.mash != "" else "MashStep"
                    step_string = {
                        "name": str(row[0]),
                        "props": {
                            "AutoMode": self.AutoMode,
                            "Kettle": self.id,
                            "Sensor": self.kettle.sensor,
                            "Temp": (
                                str(int(row[1]))
                                if self.TEMP_UNIT == "C"
                                else str(round(9.0 / 5.0 * int(row[1]) + 32))
                            ),
                            "Timer": str(int(row[2])),
                        },
                        "status_text": "",
                        "status": "I",
                        "type": step_type,
                    }
                    await self.create_step(step_string)

                # MashOut -> Notification step that sends notification and waits for user input to move to next step (AutoNext=No)
                if self.mashout == "NotificationStep":
                    step_string = {
                        "name": "Lautering",
                        "props": {
                            "AutoNext": "No",
                            "Kettle": self.id,
                            "Notification": "Mash Process completed. Please start lautering and press next to start boil.",
                        },
                        "status_text": "",
                        "status": "I",
                        "type": self.mashout,
                    }
                    await self.create_step(step_string)

                # create a boil step with hop alarms and alarm for first wort hops
                Hops = self.getBoilAlerts(hops, miscs, "kbh")
                step_type = self.boil if self.boil != "" else "BoilStep"
                step_string = {
                    "name": "Boil Step",
                    "props": {
                        "AutoMode": self.AutoMode,
                        "Kettle": self.boilid,
                        "Sensor": self.boilkettle.sensor,
                        "Temp": int(self.BoilTemp),
                        "Timer": BoilTime,
                        "First_Wort": FirstWort[0],
                        "First_Wort_text": FirstWort[1],
                        "LidAlert": "Yes",
                        "Hop_1": Hops[0][0],
                        "Hop_1_text": Hops[0][1],
                        "Hop_2": Hops[1][0],
                        "Hop_2_text": Hops[1][1],
                        "Hop_3": Hops[2][0],
                        "Hop_3_text": Hops[2][1],
                        "Hop_4": Hops[3][0],
                        "Hop_4_text": Hops[3][1],
                        "Hop_5": Hops[4][0],
                        "Hop_5_text": Hops[4][1],
                        "Hop_6": Hops[5][0],
                        "Hop_6_text": Hops[5][1],
                    },
                    "status_text": "",
                    "status": "I",
                    "type": step_type,
                }

                await self.create_step(step_string)

                # create a whirlpool step with optional cooldown
                if not whirlpool:
                    await self.create_Whirlpool_Cooldown()
                else:
                    await self.create_Whirlpool_Cooldown(
                        str(abs(whirlpool[0][0]))
                    )  # from kbh this value comes as negative but must be positive

                self.cbpi.notify("KBH Recipe created", name, NotificationType.INFO)

            except Exception as e:
                self.cbpi.notify(
                    "KBH Recipe creation failure: {}".format(e),
                    name,
                    NotificationType.ERROR,
                )
                pass
        else:
            self.cbpi.notify(
                "Recipe Upload",
                "No default Kettle defined. Please specify default Kettle in settings",
                NotificationType.ERROR,
            )

    def findMax(self, string):
        self.path = self.cbpi.config_folder.get_upload_file("mmum.json")
        e = json.load(open(self.path))
        for idx in range(1, 20):
            search_string = string.replace("%%", str(idx))
            i = idx
            if search_string not in e:
                break
        return i

    def getJsonMashin(self, id):
        self.path = self.cbpi.config_folder.get_upload_file("mmum.json")
        e = json.load(open(self.path))
        return float(e["Infusion_Einmaischtemperatur"])

    # function to create a recipe from a MUMM json recipe file
    async def json_recipe_creation(self, Recipe_ID):
        config = self.get_config_values()
        try:
            if self.kettle is not None:
                # check if  mmum-json file is located in upload folder and load it
                self.path = self.cbpi.config_folder.get_upload_file("mmum.json")
                if os.path.exists(self.path) is False:
                    self.cbpi.notify(
                        "File Not Found",
                        "Please upload a MMuM-JSON File",
                        NotificationType.ERROR,
                    )

                e = json.load(open(self.path))
                name = e["Name"]
                boil_time = float(e["Kochzeit_Wuerze"])

                await self.create_recipe(name)

                # get the hop addition times
                hops = []
                for idx in range(1, self.findMax("Hopfen_%%_Kochzeit")):
                    hops_name = "%sg %s %s%% alpha" % (
                        e["Hopfen_{}_Menge".format(idx)],
                        e["Hopfen_{}_Sorte".format(idx)],
                        e["Hopfen_{}_alpha".format(idx)],
                    )
                    if e["Hopfen_{}_Kochzeit".format(idx)].isnumeric():
                        if (
                            boil_time
                            is not e["Hopfen_{}_Kochzeit".format(idx)].isnumeric()
                        ):
                            alert = float(e["Hopfen_{}_Kochzeit".format(idx)])
                    elif (
                        e["Hopfen_{}_Kochzeit".format(idx)] == "Whirlpool"
                        or float(e["Hopfen_{}_Kochzeit".format(idx)]) < 0
                    ):
                        alert = float(0)
                        hops_name = hops_name + " whirlpool"
                    else:
                        self.cbpi.notify(
                            "No Number at Hoptime",
                            "Please change json-File at Hopfen_{}_Kochzeit".format(idx),
                            NotificationType.ERROR,
                        )
                        alert = float(0)
                    hops.append({"name": hops_name, "time": alert})

                firstHops = []
                for idx in range(1, self.findMax("Hopfen_VWH_%%_Sorte")):
                    firstHops_name = "%sg %s %s%% alpha" % (
                        e["Hopfen_VWH_{}_Menge".format(idx)],
                        e["Hopfen_VWH_{}_Sorte".format(idx)],
                        e["Hopfen_VWH_{}_alpha".format(idx)],
                    )

                    firstHops.append({"name": firstHops_name})

                FirstWort = self.getFirstWort(firstHops, "json")

                miscs = []
                for idx in range(1, self.findMax("WeitereZutat_Wuerze_%%_Kochzeit")):
                    miscs_name = "%s%s %s" % (
                        e["WeitereZutat_Wuerze_{}_Menge".format(idx)],
                        e["WeitereZutat_Wuerze_{}_Einheit".format(idx)],
                        e["WeitereZutat_Wuerze_{}_Name".format(idx)],
                    )
                    if e["WeitereZutat_Wuerze_{}_Kochzeit".format(idx)].isnumeric():
                        alert = float(e["WeitereZutat_Wuerze_{}_Kochzeit".format(idx)])
                    elif (
                        e["WeitereZutat_Wuerze_{}_Kochzeit".format(idx)] == "Whirlpool"
                        or float(e["WeitereZutat_Wuerze_{}_Kochzeit".format(idx)]) < 0
                    ):
                        alert = float(0)
                        miscs_name = miscs_name + " whirlpool"
                    else:
                        self.api.notify(
                            headline="No Number at Hoptime",
                            message="Please change json-File at WeitereZutat_Wuerze_{}_Kochzeit".format(
                                idx
                            ),
                            type="danger",
                        )
                        alert = float(0)
                    miscs.append({"name": miscs_name, "time": alert})

                # Mash Steps -> first step is different as it heats up to defined temp and stops with notification to add malt
                # AutoMode is yes to start and stop automatic mode or each step
                MashIn_Flag = True
                step_kettle = self.id
                last_step_temp = 0
                logging.info(
                    step_kettle
                )  ###################################################
                for row in self.getSteps(Recipe_ID, "json"):
                    step_name = str(row.get("name"))
                    step_timer = str(int(row.get("timer")))
                    step_temp = str(int(row.get("temp")))
                    last_step_temp = step_temp
                    sensor = self.kettle.sensor
                    if MashIn_Flag == True:
                        if row.get("timer") == 0:
                            step_type = (
                                self.mashin if self.mashin != "" else "MashInStep"
                            )
                            Notification = (
                                "Target temperature reached. Please add malt."
                            )
                            MashIn_Flag = False
                            if step_name is None or step_name == "":
                                step_name = "MashIn"
                        elif self.addmashin == "Yes":
                            step_type = (
                                self.mashin if self.mashin != "" else "MashInStep"
                            )
                            Notification = (
                                "Target temperature reached. Please add malt."
                            )
                            MashIn_Flag = False
                            step_string = {
                                "name": "MashIn",
                                "props": {
                                    "AutoMode": self.AutoMode,
                                    "Kettle": self.id,
                                    "Sensor": self.kettle.sensor,
                                    "Temp": self.getJsonMashin(Recipe_ID),
                                    "Timer": 0,
                                    "Notification": Notification,
                                },
                                "status_text": "",
                                "status": "I",
                                "type": step_type,
                            }
                            await self.create_step(step_string)
                            logging.info(
                                step_kettle
                            )  ###################################################

                            step_type = self.mash if self.mash != "" else "MashStep"
                            Notification = ""
                        else:
                            step_type = self.mash if self.mash != "" else "MashStep"
                            Notification = ""

                    else:
                        step_type = self.mash if self.mash != "" else "MashStep"
                        Notification = ""

                    step_string = {
                        "name": step_name,
                        "props": {
                            "AutoMode": self.AutoMode,
                            "Kettle": self.id,
                            "Sensor": self.kettle.sensor,
                            "Temp": step_temp,
                            "Timer": step_timer,
                            "Notification": Notification,
                        },
                        "status_text": "",
                        "status": "I",
                        "type": step_type,
                    }

                    await self.create_step(step_string)
                # MashOut -> mashStep to reach mashout-temp for 1 min
                if last_step_temp != e["Abmaischtemperatur"]:
                    step_string = {
                        "name": "MashOut",
                        "props": {
                            "AutoMode": self.AutoMode,
                            "Kettle": self.id,
                            "Sensor": self.kettle.sensor,
                            "Temp": e["Abmaischtemperatur"],
                            "Timer": 1,
                            "Notification": "",
                        },
                        "status_text": "",
                        "status": "I",
                        "type": "MashStep",
                    }

                    await self.create_step(step_string)
                # Lautering -> Simple step that sends notification and waits for user input to move to next step (AutoNext=No)
                if self.mashout == "NotificationStep":
                    step_string = {
                        "name": "Lautering",
                        "props": {
                            "AutoNext": "No",
                            "Kettle": self.id,
                            "Notification": "Mash Process completed. Please start lautering and press next to start boil.",
                        },
                        "status_text": "",
                        "status": "I",
                        "type": self.mashout,
                    }
                    await self.create_step(step_string)

                # Measure Original Gravity -> Simple step that sends notification
                step_string = {
                    "name": "Measure Original Gravity",
                    "props": {
                        "AutoNext": "No",
                        "Kettle": self.id,
                        "Notification": "What is the original gravity of the beer wort?",
                    },
                    "status_text": "",
                    "status": "I",
                    "type": "NotificationStep",
                }
                await self.create_step(step_string)

                # Boil step including hop alarms and alarm for first wort hops -> Automode is set tu yes
                Hops = self.getBoilAlerts(hops, miscs, "json")
                step_kettle = self.boilid
                step_type = self.boil if self.boil != "" else "BoilStep"
                step_time = str(int(boil_time))
                step_temp = self.BoilTemp
                sensor = self.boilkettle.sensor
                LidAlert = "Yes"

                logging.info(
                    step_temp
                )  ###################################################

                step_string = {
                    "name": "Boil Step",
                    "props": {
                        "AutoMode": self.AutoMode,
                        "Kettle": step_kettle,
                        "Sensor": sensor,
                        "Temp": step_temp,
                        "Timer": step_time,
                        "First_Wort": FirstWort[0],
                        "First_Wort_text": FirstWort[1],
                        "LidAlert": LidAlert,
                        "Hop_1": Hops[0][0],
                        "Hop_1_text": Hops[0][1],
                        "Hop_2": Hops[1][0],
                        "Hop_2_text": Hops[1][1],
                        "Hop_3": Hops[2][0],
                        "Hop_3_text": Hops[2][1],
                        "Hop_4": Hops[3][0],
                        "Hop_4_text": Hops[3][1],
                        "Hop_5": Hops[4][0],
                        "Hop_5_text": Hops[4][1],
                        "Hop_6": Hops[5][0],
                        "Hop_6_text": Hops[5][1],
                    },
                    "status_text": "",
                    "status": "I",
                    "type": step_type,
                }

                await self.create_step(step_string)

                # Measure Original Gravity -> Simple step that sends notification
                step_string = {
                    "name": "Measure Original Gravity",
                    "props": {
                        "AutoNext": "No",
                        "Kettle": self.id,
                        "Notification": "What is the original gravity of the beer wort?",
                    },
                    "status_text": "",
                    "status": "I",
                    "type": "NotificationStep",
                }
                await self.create_step(step_string)

                await self.create_Whirlpool_Cooldown()

                self.cbpi.notify(
                    "MMuM-JSON Recipe created ", name, NotificationType.INFO
                )
            else:
                self.cbpi.notify(
                    "Recipe Upload",
                    "No default Kettle defined. Please specify default Kettle in settings",
                    NotificationType.ERROR,
                )
        except Exception as e:
            self.cbpi.notify(
                "MMuM-JSON Recipe creation failure: {}".format(e),
                name,
                NotificationType.ERROR,
            )
            logger.error(e)

    async def xml_recipe_creation(self, Recipe_ID):
        config = self.get_config_values()
        try:
            if self.kettle is not None:
                # load beerxml file located in upload folder
                self.path = self.cbpi.config_folder.get_upload_file("beer.xml")
                if os.path.exists(self.path) is False:
                    self.cbpi.notify(
                        "File Not Found",
                        "Please upload a Beer.xml File",
                        NotificationType.ERROR,
                    )

                e = xml.etree.ElementTree.parse(self.path).getroot()
                recipe = e.find("./RECIPE[%s]" % (str(Recipe_ID)))
                hops = recipe.findall("./HOPS/HOP")
                miscs = recipe.findall('MISCS/MISC[USE="Boil"]')
                name = e.find("./RECIPE[%s]/NAME" % (str(Recipe_ID))).text
                boil_time = float(
                    e.find("./RECIPE[%s]/BOIL_TIME" % (str(Recipe_ID))).text
                )
                FirstWort = self.getFirstWort(hops, "xml")

                await self.create_recipe(name)
                # Mash Steps -> first step is different as it heats up to defined temp and stops with notification to add malt
                # AutoMode is yes to start and stop automatic mode or each step
                MashIn_Flag = True
                step_kettle = self.id
                for row in self.getSteps(Recipe_ID, "xml"):
                    step_name = str(row.get("name"))
                    step_timer = str(int(row.get("timer")))
                    step_temp = str(int(row.get("temp")))
                    sensor = self.kettle.sensor
                    if MashIn_Flag == True:
                        if row.get("timer") == 0:
                            step_type = (
                                self.mashin if self.mashin != "" else "MashInStep"
                            )
                            Notification = (
                                "Target temperature reached. Please add malt."
                            )
                            MashIn_Flag = False
                            if step_name is None or step_name == "":
                                step_name = "MashIn"
                        elif self.addmashin == "Yes":
                            step_type = (
                                self.mashin if self.mashin != "" else "MashInStep"
                            )
                            Notification = (
                                "Target temperature reached. Please add malt."
                            )
                            MashIn_Flag = False
                            step_string = {
                                "name": "MashIn",
                                "props": {
                                    "AutoMode": self.AutoMode,
                                    "Kettle": self.id,
                                    "Sensor": self.kettle.sensor,
                                    "Temp": step_temp,
                                    "Timer": 0,
                                    "Notification": Notification,
                                },
                                "status_text": "",
                                "status": "I",
                                "type": step_type,
                            }
                            await self.create_step(step_string)

                            step_type = self.mash if self.mash != "" else "MashStep"
                            Notification = ""
                        else:
                            step_type = self.mash if self.mash != "" else "MashStep"
                            Notification = ""

                    else:
                        step_type = self.mash if self.mash != "" else "MashStep"
                        Notification = ""

                    step_string = {
                        "name": step_name,
                        "props": {
                            "AutoMode": self.AutoMode,
                            "Kettle": self.id,
                            "Sensor": self.kettle.sensor,
                            "Temp": step_temp,
                            "Timer": step_timer,
                            "Notification": Notification,
                        },
                        "status_text": "",
                        "status": "I",
                        "type": step_type,
                    }

                    await self.create_step(step_string)

                # MashOut -> Simple step that sends notification and waits for user input to move to next step (AutoNext=No)
                if self.mashout == "NotificationStep":
                    step_string = {
                        "name": "Lautering",
                        "props": {
                            "AutoNext": "No",
                            "Kettle": self.id,
                            "Notification": "Mash Process completed. Please start lautering and press next to start boil.",
                        },
                        "status_text": "",
                        "status": "I",
                        "type": self.mashout,
                    }
                    await self.create_step(step_string)

                # Boil step including hop alarms and alarm for first wort hops -> Automode is set tu yes
                Hops = self.getBoilAlerts(hops, miscs, "xml")
                step_kettle = self.boilid
                step_type = self.boil if self.boil != "" else "BoilStep"
                step_time = str(int(boil_time))
                step_temp = self.BoilTemp
                sensor = self.boilkettle.sensor
                LidAlert = "Yes"

                step_string = {
                    "name": "Boil Step",
                    "props": {
                        "AutoMode": self.AutoMode,
                        "Kettle": step_kettle,
                        "Sensor": sensor,
                        "Temp": step_temp,
                        "Timer": step_time,
                        "First_Wort": FirstWort[0],
                        "First_Wort_text": FirstWort[1],
                        "LidAlert": LidAlert,
                        "Hop_1": Hops[0][0],
                        "Hop_1_text": Hops[0][1],
                        "Hop_2": Hops[1][0],
                        "Hop_2_text": Hops[1][1],
                        "Hop_3": Hops[2][0],
                        "Hop_3_text": Hops[2][1],
                        "Hop_4": Hops[3][0],
                        "Hop_4_text": Hops[3][1],
                        "Hop_5": Hops[4][0],
                        "Hop_5_text": Hops[4][1],
                        "Hop_6": Hops[5][0],
                        "Hop_6_text": Hops[5][1],
                    },
                    "status_text": "",
                    "status": "I",
                    "type": step_type,
                }

                await self.create_step(step_string)

                await self.create_Whirlpool_Cooldown()

                self.cbpi.notify("BeerXML Recipe created ", name, NotificationType.INFO)
            else:
                self.cbpi.notify(
                    "Recipe Upload",
                    "No default Kettle defined. Please specify default Kettle in settings",
                    NotificationType.ERROR,
                )
        except Exception as e:
            self.cbpi.notify(
                "BeerXML Recipe creation failure: {}".format(e),
                name,
                NotificationType.ERROR,
            )
            logger.error(e)
            pass

    # XML functions to retrieve xml repice parameters (if multiple recipes are stored in one xml file, id could be used)
    def getSteps(self, id, recipe_type):
        steps = []
        if recipe_type == "xml":
            e = xml.etree.ElementTree.parse(self.path).getroot()
            for e in e.findall("./RECIPE[%s]/MASH/MASH_STEPS/MASH_STEP" % (str(id))):
                if self.cbpi.config.get("TEMP_UNIT", "C") == "C":
                    temp = float(e.find("STEP_TEMP").text)
                else:
                    temp = round(9.0 / 5.0 * float(e.find("STEP_TEMP").text) + 32, 2)
                steps.append(
                    {
                        "name": e.find("NAME").text,
                        "temp": temp,
                        "timer": float(e.find("STEP_TIME").text),
                    }
                )
        elif recipe_type == "json":
            self.path = self.cbpi.config_folder.get_upload_file("mmum.json")
            e = json.load(open(self.path))
            for idx in range(1, self.findMax("Infusion_Rastzeit%%")):
                if self.cbpi.config.get("TEMP_UNIT", "C") == "C":
                    temp = float(e["Infusion_Rasttemperatur{}".format(idx)])
                else:
                    temp = round(
                        9.0 / 5.0 * float(e["Infusion_Rasttemperatur{}".format(idx)])
                        + 32,
                        2,
                    )

                steps.append(
                    {
                        "name": "Rast {}".format(idx),
                        "temp": temp,
                        "timer": float(e["Infusion_Rastzeit{}".format(idx)]),
                    }
                )

        return steps

    async def bf_recipe_creation(self, Recipe_ID):
        config = self.get_config_values()

        if self.kettle is not None:

            brewfather = True
            result = []
            self.bf_url = "https://api.brewfather.app/v2/recipes/" + Recipe_ID
            brewfather_user_id = self.cbpi.config.get("brewfather_user_id", None)
            if brewfather_user_id == "" or brewfather_user_id is None:
                brewfather = False

            brewfather_api_key = self.cbpi.config.get("brewfather_api_key", None)
            if brewfather_api_key == "" or brewfather_api_key is None:
                brewfather = False

            if brewfather == True:
                encodedData = base64.b64encode(
                    bytes(f"{brewfather_user_id}:{brewfather_api_key}", "ISO-8859-1")
                ).decode("ascii")
                headers = {"Authorization": "Basic %s" % encodedData}
                bf_recipe = ""

                async with aiohttp.ClientSession(headers=headers) as bf_session:
                    async with bf_session.get(self.bf_url) as r:
                        bf_recipe = await r.json()
                    await bf_session.close()

            if bf_recipe != "":
                try:
                    StrikeTemp = bf_recipe["data"]["strikeTemp"]
                except:
                    StrikeTemp = None
                # BF is sending all Temeprature values in °C. If system is running in F, values need to be converted
                if StrikeTemp is not None and self.TEMP_UNIT != "C":
                    StrikeTemp = round((9.0 / 5.0 * float(StrikeTemp) + 32))

                RecipeName = bf_recipe["name"]
                BoilTime = bf_recipe["boilTime"]
                mash_steps = bf_recipe["mash"]["steps"]
                hops = bf_recipe["hops"]
                try:
                    miscs = bf_recipe["miscs"]
                except:
                    miscs = None

                try:
                    fermentation_steps = bf_recipe["fermentation"]["steps"]
                except:
                    fermentation_steps = None

                if fermentation_steps is not None:
                    try:
                        step = fermentation_steps[0]
                        self.fermentation_step_temp = int(step["stepTemp"])
                    except:
                        self.fermentation_step_temp = None

                if self.fermentation_step_temp is not None and self.TEMP_UNIT != "C":
                    self.fermentation_step_temp = round(
                        (9.0 / 5.0 * float(self.fermentation_step_temp) + 32)
                    )

                FirstWort = self.getFirstWort(hops, "bf")

                await self.create_recipe(RecipeName)

                # Mash Steps -> first step is different as it heats up to defined temp and stops with notification to add malt
                # AutoMode is yes to start and stop automatic mode or each step
                MashIn_Flag = True
                step_kettle = self.id
                for step in mash_steps:
                    try:
                        step_name = step["name"]
                        if step_name == "":
                            step_name = "MashStep"
                    except:
                        step_name = "MashStep"

                    step_timer = str(int(step["stepTime"]))

                    if self.TEMP_UNIT == "C":
                        step_temp = str(int(step["stepTemp"]))
                    else:
                        step_temp = str(round((9.0 / 5.0 * int(step["stepTemp"]) + 32)))

                    sensor = self.kettle.sensor
                    if MashIn_Flag == True:

                        if int(step_timer) == 0:
                            step_type = (
                                self.mashin if self.mashin != "" else "MashInStep"
                            )
                            Notification = (
                                "Target temperature reached. Please add malt."
                            )
                            MashIn_Flag = False

                        elif self.addmashin == "Yes":
                            mashin_temp = (
                                str(round(StrikeTemp))
                                if StrikeTemp is not None
                                else step_temp
                            )
                            step_type = (
                                self.mashin if self.mashin != "" else "MashInStep"
                            )
                            Notification = (
                                "Target temperature reached. Please add malt."
                            )
                            MashIn_Flag = False
                            step_string = {
                                "name": "MashIn",
                                "props": {
                                    "AutoMode": self.AutoMode,
                                    "Kettle": self.id,
                                    "Sensor": self.kettle.sensor,
                                    "Temp": mashin_temp,
                                    "Timer": 0,
                                    "Notification": Notification,
                                },
                                "status_text": "",
                                "status": "I",
                                "type": step_type,
                            }
                            await self.create_step(step_string)

                            step_type = self.mash if self.mash != "" else "MashStep"
                            Notification = ""
                        else:
                            step_type = self.mash if self.mash != "" else "MashStep"
                            Notification = ""

                    else:
                        step_type = self.mash if self.mash != "" else "MashStep"
                        Notification = ""

                    step_string = {
                        "name": step_name,
                        "props": {
                            "AutoMode": self.AutoMode,
                            "Kettle": self.id,
                            "Sensor": self.kettle.sensor,
                            "Temp": step_temp,
                            "Timer": step_timer,
                            "Notification": Notification,
                        },
                        "status_text": "",
                        "status": "I",
                        "type": step_type,
                    }

                    await self.create_step(step_string)

                # MashOut -> Simple step that sends notification and waits for user input to move to next step (AutoNext=No)

                if self.mashout == "NotificationStep":
                    step_string = {
                        "name": "Lautering",
                        "props": {
                            "AutoNext": "No",
                            "Kettle": self.id,
                            "Notification": "Mash Process completed. Please start lautering and press next to start boil.",
                        },
                        "status_text": "",
                        "status": "I",
                        "type": self.mashout,
                    }
                await self.create_step(step_string)

                # Boil step including hop alarms and alarm for first wort hops -> Automode is set tu yes
                Hops = self.getBoilAlerts(hops, miscs, "bf")

                step_kettle = self.boilid
                step_time = str(int(BoilTime))
                step_type = self.boil if self.boil != "" else "BoilStep"
                step_temp = self.BoilTemp
                sensor = self.boilkettle.sensor
                LidAlert = "Yes"

                step_string = {
                    "name": "Boil Step",
                    "props": {
                        "AutoMode": self.AutoMode,
                        "Kettle": step_kettle,
                        "Sensor": sensor,
                        "Temp": step_temp,
                        "Timer": step_time,
                        "First_Wort": FirstWort[0],
                        "First_Wort_text": FirstWort[1],
                        "LidAlert": LidAlert,
                        "Hop_1": Hops[0][0],
                        "Hop_1_text": Hops[0][1],
                        "Hop_2": Hops[1][0],
                        "Hop_2_text": Hops[1][1],
                        "Hop_3": Hops[2][0],
                        "Hop_3_text": Hops[2][1],
                        "Hop_4": Hops[3][0],
                        "Hop_4_text": Hops[3][1],
                        "Hop_5": Hops[4][0],
                        "Hop_5_text": Hops[4][1],
                        "Hop_6": Hops[5][0],
                        "Hop_6_text": Hops[5][1],
                    },
                    "status_text": "",
                    "status": "I",
                    "type": step_type,
                }

                await self.create_step(step_string)

                await self.create_Whirlpool_Cooldown()

                self.cbpi.notify(
                    "Brewfather App Recipe created: ", RecipeName, NotificationType.INFO
                )
        else:
            self.cbpi.notify(
                "Recipe Upload",
                "No default Kettle defined. Please specify default Kettle in settings",
                NotificationType.ERROR,
            )

    def getBoilAlerts(self, hops, miscs, recipe_type):
        alerts = []
        for hop in hops:
            if recipe_type == "xml":
                use = hop.find("USE").text
                ## Hops which are not used in the boil step should not cause alerts
                if use != "Aroma" and use != "Boil":
                    continue
                alerts.append([float(hop.find("TIME").text), hop.find("NAME").text])
            elif recipe_type == "bf":
                use = hop["use"]
                if use != "Aroma" and use != "Boil":
                    continue
                alerts.append([float(hop["time"]), hop["name"]])  ## TODO: Testing
            elif recipe_type == "kbh":
                alerts.append([float(hop[0]), hop[1]])
            elif recipe_type == "json":
                alerts.append([float(hop["time"]), hop["name"]])

        ## There might also be miscelaneous additions during boild time
        if miscs is not None:
            for misc in miscs:
                if recipe_type == "xml":
                    alerts.append(
                        [float(misc.find("TIME").text), misc.find("NAME").text]
                    )
                elif recipe_type == "bf":
                    use = misc["use"]
                    if use != "Aroma" and use != "Boil":
                        continue
                    alerts.append([float(misc["time"]), misc["name"]])  ## TODO: Testing
                elif recipe_type == "kbh":
                    alerts.append([float(misc[0]), misc[1]])
                elif recipe_type == "json":
                    alerts.append([float(misc["time"]), misc["name"]])
        ## Dedupe and order the additions by their time
        ## CBP should have these additions in reverse
        alerts = sorted(alerts, key=lambda x: x[0], reverse=True)
        hop_alerts = [
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
        ]
        for i in range(0, 6):
            try:
                if float(alerts[i][0]) > -1:
                    hop_alerts[i] = alerts[i]
            except:
                pass
        return hop_alerts

    def getFirstWort(self, hops, recipe_type):
        alert = "No"
        names = []
        if recipe_type == "kbh":
            if len(hops) != 0:
                alert = "Yes"
                for hop in hops:
                    names.append(hop[1])
        elif recipe_type == "xml":
            for hop in hops:
                use = hop.find("USE").text
                ## Hops which are not used in the boil step should not cause alerts
                if use != "First Wort":
                    continue
                alert = "Yes"
                names.append(hop.find("NAME").text)
        elif recipe_type == "bf":
            for hop in hops:
                if hop["use"] == "First Wort":
                    alert = "Yes"
                    names.append(hop["name"])  ## TODO: Testing
        elif recipe_type == "json":
            if len(hops) != 0:
                alert = "Yes"
                for hop in hops:
                    names.append(hop["name"])

        return [alert, " and ".join(names)]

    async def create_Whirlpool_Cooldown(self, time: str = "15"):
        # Add Waitstep as Whirlpool
        if self.cooldown != "WaiStep" and self.cooldown != "":
            step_string = {
                "name": "Whirlpool",
                "props": {"Kettle": self.boilid, "Timer": time},
                "status_text": "",
                "status": "I",
                "type": "WaitStep",
            }
            await self.create_step(step_string)

        # CoolDown step is sending a notification when cooldowntemp is reached
        step_type = self.cooldown if self.cooldown != "" else "WaitStep"
        step_name = "CoolDown"
        cooldown_sensor = ""
        step_temp = ""
        step_timer = time
        if step_type == "CooldownStep":
            cooldown_sensor = self.cbpi.config.get("steps_cooldown_sensor", None)
            if cooldown_sensor is None or cooldown_sensor == "":
                cooldown_sensor = (
                    self.boilkettle.sensor
                )  # fall back to boilkettle sensor if no other sensor is specified
            step_timer = ""
            try:
                step_temp = (
                    int(self.CoolDownTemp)
                    if (
                        self.fermentation_step_temp is None
                        or self.fermentation_step_temp <= int(self.CoolDownTemp)
                    )
                    else self.fermentation_step_temp
                )
            except:
                step_temp = int(self.CoolDownTemp)
            step_string = {
                "name": "Cooldown",
                "props": {
                    "Kettle": self.boilid,
                    "Timer": step_timer,
                    "Temp": step_temp,
                    "Sensor": cooldown_sensor,
                    "Actor": self.CoolDownActor,
                },
                "status_text": "",
                "status": "I",
                "type": step_type,
            }
            await self.create_step(step_string)

    def get_config_values(self):
        self.kettle = None
        self.boilkettle = None
        # Define MashSteps
        self.TEMP_UNIT = self.cbpi.config.get("TEMP_UNIT", "C")
        self.AutoMode = self.cbpi.config.get("AutoMode", "Yes")
        self.mashin = self.cbpi.config.get("steps_mashin", "MashInStep")
        self.mash = self.cbpi.config.get("steps_mash", "MashStep")
        self.mashout = self.cbpi.config.get(
            "steps_mashout", None
        )  # Currently used only for the Braumeister
        self.boil = self.cbpi.config.get("steps_boil", "BoilStep")
        self.whirlpool = "Waitstep"
        self.cooldown = self.cbpi.config.get("steps_cooldown", "WaitStep")
        # get default boil temp from settings
        self.BoilTemp = self.cbpi.config.get("steps_boil_temp", 98)
        # get default cooldown temp alarm setting
        self.CoolDownTemp = self.cbpi.config.get("steps_cooldown_temp", 25)
        self.CoolDownActor = self.cbpi.config.get("steps_cooldown_actor", None)
        # get default Kettle from Settings
        self.id = self.cbpi.config.get("MASH_TUN", None)
        self.boilid = self.cbpi.config.get("BoilKettle", None)
        if self.boilid is None:
            self.boilid = self.id
        # If next parameter is Yes, MashIn Ste will be added before first mash step if not included in recipe
        self.addmashin = self.cbpi.config.get("AddMashInStep", "Yes")

        try:
            self.kettle = self.cbpi.kettle.find_by_id(self.id)
        except:
            self.cbpi.notify(
                "Recipe Upload",
                "No default Kettle defined. Please specify default Kettle in settings",
                NotificationType.ERROR,
            )
        try:
            self.boilkettle = self.cbpi.kettle.find_by_id(self.boilid)
        except:
            self.boilkettle = self.kettle

        config_values = {
            "kettle": self.kettle,
            "kettle_id": str(self.id),
            "boilkettle": self.boilkettle,
            "boilkettle_id": str(self.boilid),
            "mashin": str(self.mashin),
            "mash": str(self.mash),
            "mashout": str(self.mashout),
            "boil": str(self.boil),
            "whirlpool": str(self.whirlpool),
            "cooldown": str(self.cooldown),
            "boiltemp": str(self.BoilTemp),
            "cooldowntemp": str(self.CoolDownTemp),
            "cooldownactor": self.CoolDownActor,
            "temp_unit": str(self.TEMP_UNIT),
            "AutoMode": str(self.AutoMode),
        }
        logging.info(config_values)
        return config_values

    async def create_recipe(self, name):
        # Create recipe in recipe Book with name of first recipe in xml file
        self.recipeID = await self.cbpi.recipe.create(name)
        # send recipe to mash profile
        await self.cbpi.recipe.brew(self.recipeID)
        # remove empty recipe from recipe book
        await self.cbpi.recipe.remove(self.recipeID)

    # function to create json to be send to api to add a step to the current mash profile. Currently all properties are send to each step which does not cuase an issue
    async def create_step(self, step_string):
        # get server port from settings and define url for api calls -> adding steps
        self.port = str(self.cbpi.static_config.get("port", 8000))
        self.url = "http://127.0.0.1:" + self.port + "/step2/"
        # convert step:string to json required for api call.
        step = json.dumps(step_string)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(self.url, data=step) as response:
                return await response.text()
            await self.push_update()


def setup(cbpi):
    cbpi.plugin.register("RecipeCreation", RecipeCreation)
    pass

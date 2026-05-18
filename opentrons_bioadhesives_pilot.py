from opentrons import protocol_api

metadata = {
    "apiLevel": "2.15",
    "protocolName": "Viscous Reagent Dispensing Protocol",
    "author": "Amanda Frischmann",
    "description": "Protocol for dispensing highly viscous reagents with optimized flow rates and air expulsion for complete tip emptying.",
}

requirements = {"robotType": "Flex"}

# =============================================================================
# TRANSFER CONFIGURATION — edit only this section between the dashed lines
# Each entry is (tube_rack_source_well, plate_target_well)
# Tube rack wells available: A1, B1, A2, B2, A3, B3
# ------------------------------------------------------------------------------
TRANSFERS = [
    ("A1", "A1"),
    ("A1", "A2"),
    ("A1", "A3"),
    ("B1", "B1"),
    ("B1", "B2"),
    ("B1", "B3"),
]
# =============================================================================

# Custom tube rack definition
custom_tube_rack = {
    "ordering": [
        ["A1", "B1"],
        ["A2", "B2"],
        ["A3", "B3"]
    ],
    "brand": {
        "brand": "Custom",
        "brandId": []
    },
    "metadata": {
        "displayName": "Custom 6 Tube Rack with Generic 20 mL",
        "displayCategory": "tubeRack",
        "displayVolumeUnits": "µL",
        "tags": []
    },
    "dimensions": {
        "xDimension": 127,
        "yDimension": 85,
        "zDimension": 135
    },
    "wells": {
        "A1": {
            "depth": 58,
            "totalLiquidVolume": 20000,
            "shape": "circular",
            "diameter": 30,
            "x": 25,
            "y": 62,
            "z": 65
        },
        "B1": {
            "depth": 58,
            "totalLiquidVolume": 20000,
            "shape": "circular",
            "diameter": 30,
            "x": 25,
            "y": 22,
            "z": 65
        },
        "A2": {
            "depth": 58,
            "totalLiquidVolume": 20000,
            "shape": "circular",
            "diameter": 30,
            "x": 65,
            "y": 62,
            "z": 65
        },
        "B2": {
            "depth": 58,
            "totalLiquidVolume": 20000,
            "shape": "circular",
            "diameter": 30,
            "x": 65,
            "y": 22,
            "z": 65
        },
        "A3": {
            "depth": 58,
            "totalLiquidVolume": 20000,
            "shape": "circular",
            "diameter": 30,
            "x": 105,
            "y": 62,
            "z": 65
        },
        "B3": {
            "depth": 58,
            "totalLiquidVolume": 20000,
            "shape": "circular",
            "diameter": 30,
            "x": 105,
            "y": 22,
            "z": 65
        }
    },
    "groups": [
        {
            "brand": {
                "brand": "Generic",
                "brandId": []
            },
            "metadata": {
                "wellBottomShape": "flat",
                "displayCategory": "tubeRack"
            },
            "wells": [
                "A1",
                "B1",
                "A2",
                "B2",
                "A3",
                "B3"
            ]
        }
    ],
    "parameters": {
        "format": "irregular",
        "quirks": [],
        "isTiprack": "False",
        "isMagneticModuleCompatible": "False",
        "loadName": "jeremy_custom_6_tube_rack_20ml"
    },
    "namespace": "custom_beta",
    "version": 1,
    "schemaVersion": 2,
    "cornerOffsetFromSlot": {
        "x": 0,
        "y": 0,
        "z": 0
    }
}

def run(protocol: protocol_api.ProtocolContext):
    # Hardware setup
    # 1. P1000 tip box (Slot A2)
    tips = protocol.load_labware("opentrons_flex_96_tiprack_1000ul", "A2")
    protocol.comment("Loaded p1000 tip box in slot A2")
    
    # 2. Custom tube rack with materials (Slot B2)
    stock_rack = protocol.load_labware_from_definition(custom_tube_rack, "B2")
    protocol.comment("Loaded custom tube rack with materials in slot B2")
    
    # 3. 96-well plate for dispensing (Slot D2)
    plate = protocol.load_labware("corning_96_wellplate_360ul_flat", "D2")
    protocol.comment("Loaded 96-well plate for dispensing in slot D2")

    # Load p1000 instrument
    p1000 = protocol.load_instrument("flex_1channel_1000", "right", tip_racks=[tips])

    # Viscous reagent parameters
    flow_rate_ul_min = 150  # Slow flow rate for viscous reagents in uL/min
    volume_ul = 100         # Volume to transfer in microliters
    air_expulsion_ul = 20   # Additional air to expel after dispensing (µL)
    tip_lift_height = 8     # Height to lift tip above liquid surface (mm)

    protocol.comment(f"Starting viscous reagent dispensing at {flow_rate_ul_min} uL/min, {len(TRANSFERS)} transfers")

    # Set slow flow rates for viscous reagents (Opentrons API expects microliters per minute)
    p1000.flow_rate.aspirate = flow_rate_ul_min
    p1000.flow_rate.dispense = flow_rate_ul_min

    protocol.comment(f"Aspiration and dispense flow rates set to {flow_rate_ul_min} uL/min for viscous handling")

    # Loop through each transfer defined in TRANSFERS at the top of this file
    for source_well, target_well in TRANSFERS:
        # Pick up tip
        p1000.pick_up_tip()

        # Aspirate from the specified tube rack well
        p1000.aspirate(volume_ul, stock_rack[source_well].bottom(z=5))
        protocol.comment(f"Aspirated {volume_ul} uL from tube rack {source_well}")

        # Dispense into the specified plate well
        p1000.dispense(volume_ul, plate[target_well].bottom(z=5))
        protocol.comment(f"Dispensed {volume_ul} uL into plate well {target_well}")

        # Lift tip slightly above liquid surface for air expulsion
        p1000.move_to(plate[target_well].bottom(z=tip_lift_height))
        protocol.comment(f"Lifted tip to {tip_lift_height} mm above well {target_well} bottom")

        # Expel air to push out remaining viscous reagent from tip
        p1000.dispense(air_expulsion_ul)
        protocol.comment(f"Expelled {air_expulsion_ul} µL of air to remove remaining reagent from tip")

        # Drop tip
        p1000.drop_tip()

    protocol.comment("Viscous reagent dispensing completed")